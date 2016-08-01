import flask
from flask import request, render_template, session
from celery import Celery
from gevent.wsgi import WSGIServer
import gevent
import subprocess
import argparse as ap
from pokemongo_bot import PokemonGoBot
import data
import time
import gevent

from redis import Redis
# Init Redis
redis = Redis()

# Flask configuration
app = flask.Flask(__name__)

# Celery configuration
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

celery = Celery(
        app.name, 
        broker=app.config['CELERY_BROKER_URL'], 
        backend=app.config['CELERY_RESULT_BACKEND'])

def set_config(profile):
    base_config = data.BASE_CONFIG
    base_config['username'] = profile.get('username', '')
    base_config['password'] = profile.get('password', '')
    base_config['token'] = profile.get('token', '')
    base_config['location'] = profile.get('location', '')
    base_config['auth_service'] = profile.get('provider', '')

    release_config = data.RELEASE_POKEMON
    ignores = data.IGNORE_POKEMON
    items = data.ITEMS
    pokemon = data.POKEMON_DATA

    base_config["release_config"] = release_config
    base_config["ignores"] = ignores
    base_config["items"] = items
    base_config["pokemons"] = pokemon
    return ap.Namespace(**base_config)

@celery.task(bind=True)
def add_five_times(self, x, y):
    for i in range(4):
        z = x + y
        print self.request.id
        gevent.sleep(5)
    return x + y

@celery.task(bind=True)
def auto_play(self, profile, token):
    cfg = profile
    state = 'STARTED'
    self.update_state(state=state, meta={'status' : state})
    try: 
        bot = PokemonGoBot(cfg)
        bot.start()
        bot.take_step()
        state = 'DONE'
    except Exception as e:
        state = str(e)
    finally:
        remove_user_job(profile.username, token)
    return {'status': state}

def remove_user_job(username, token):
    redis.delete(username, token)

def user_has_job(username, token):
    return redis.execute_command('EXISTS', username, token)

def user_job(username, token):
    print redis.mget(username, token)
    task_id = redis.get(token) if redis.get(username) == token else None
    status_url = request.url_root + 'status/%s' % task_id
    return flask.jsonify({
        'status_url': status_url,
        'task_id': task_id
        })

def remove_job(username, token):
    redis.delete(username, token)

def start_new_job(form):
    username = form.get('username','')
    token = form.get('token', '')
    if not user_has_job(username, token):
        print "new job: ", username, token
        cfg = set_config(form)
        task = auto_play.delay(cfg, token)
        redis.msetnx({
            username: token,
            token: task.id
            })
        status_url = request.url_root + 'status/%s' % task.id
        return flask.jsonify({
            'status_url': status_url,
            'task_id': task.id
            })

def sessionInfo():
    print "get session info: " + str(session)
    return session['username'], session['token']

@app.route('/', methods=['GET'])
def index():
    def prefill():
        return { 
            "username": session.get('username',''),
            "gps": session.get('gps',''),
            "token": session.get('token', '')
            }
    base = request.url_root
    url = base + 'start'
    return render_template('index.html', **prefill())

@app.route('/start', methods=['POST'])
def start():
    def update_session(form):
        session['username'] = form.get('username', '')
        session['gps'] = form.get('gps','')
        session['token'] = form.get('token', '')
    form = request.form
    username = form.get('username','')
    token = form.get('token', '')
    update_session(form)
    if user_has_job(username, token):
        print "user has job"
        return user_job(username, token)
    else:
        print "user has no job"
        return start_new_job(form)

@app.route('/status/<task_id>')
def status(task_id):
    username, token = sessionInfo()
    if user_has_job(username, token):
        try:
            task = auto_play.AsyncResult(task_id)
            status = task.info.get('status') if task.info else task.status
        except Exception as e:
            status = str(e)
    else:
        status = 'Job not found'
    return 'status %s' % status

@app.route('/stop/<task_id>')
def stop(task_id):
    username, token = sessionInfo()
    if user_has_job(username, token):
        task = auto_play.AsyncResult(task_id)
        task.revoke(terminate=True)
        remove_user_job(username, token)
        return 'bot for %s stopped' % username
    else:
        return 'Job not found'

if __name__ == '__main__':
    app.debug = True
    app.secret_key = 'top-secret'
    http_server = WSGIServer(('', 5000), app)
    http_server.serve_forever()
