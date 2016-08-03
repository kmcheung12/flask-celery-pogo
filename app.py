import flask
from flask import request, render_template, session
from celery import Celery
from gevent.pywsgi import WSGIServer
import gevent
import subprocess
import argparse as ap
from pokemongo_bot import PokemonGoBot
from pgoapi.exceptions import NotLoggedInException
import data
import time
from hashlib import sha256
import logging

from redis import Redis
# Init Redis
redis = Redis()

# Flask configuration
app = flask.Flask(__name__)

# Celery configuration
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
app.config['CELERYD_PREFETCH_MULTIPLIER'] = 0

celery = Celery(
        app.name, 
        broker=app.config['CELERY_BROKER_URL'], 
        backend=app.config['CELERY_RESULT_BACKEND'])

class RedisHandler(logging.Handler):
    def __init__(self, host='localhost', history=2000):
         logging.Handler.__init__(self)
         self.redis = Redis(host)
         self.formatter = logging.Formatter("%(asctime)s - %(message)s")
         self.history = 2000
         self.entries = 0

    def emit(self, record):
        self.redis.rpush(record.name, self.format(record))
        self.entries += 1
        if self.entries > 2 * self.history:
            self.redis.ltrim(record.name, -self.history, -1)


def set_config(profile):
    base_config = data.BASE_CONFIG
    base_config['username'] = profile.get('username', '')
    base_config['password'] = profile.get('password', '')
    base_config['token'] = profile.get('token', '')
    base_config['location'] = profile.get('location', '')
    # base_config['auth_service'] = profile.get('provider', '')
    base_config['auth_service'] = 'ptc'

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
    user = profile.username
    state = 'STARTED'
    self.update_state(state=state, meta={'status' : state})

    logger = logging.getLogger(self.request.id+' log')
    logger.setLevel(logging.INFO)
    logger.addHandler(RedisHandler())

    def run_bot():
        try: 
            bot = PokemonGoBot(cfg)
            bot.setup_logging(logger)
            bot.start()
            while True:
                bot.take_step()
        except KeyError:
            logger.info('Pokemon server return incomplete data. Re-login')
        except NotLoggedInException:
            logger.info('Token expired. Re-login')
    try:
        for i in range(10):
            run_bot()
    finally:
        logger.info( 'remove user job: %s'% token)
        remove_user_job(user, token)
    return {'status': state}

def tokenHash(username, token):
    return 'token-' + sha256(username + token).hexdigest()

def userHash(username):
    return 'user-' + sha256(username).hexdigest()

def remove_user_job(username, token):
    redis.delete(userHash(username), tokenHash(username, token))

def user_has_job(username, token):
    return redis.execute_command('EXISTS', userHash(username)) and redis.execute_command('EXISTS', tokenHash(username, token))

def user_job(username, token):
    print redis.mget(username, token)
    task_id = redis.get(tokenHash(username, token)) if redis.get(userHash(username)) == tokenHash(username, token) else None
    status_url = request.url_root + 'status/%s' % task_id
    return flask.jsonify({
        'status_url': status_url,
        'task_id': task_id
        })

def remove_job(username, token):
    redis.delete(userHash(username), tokenHash(username, token))

def start_new_job(form):
    username = form.get('username','')
    token = form.get('token', '')
    if not user_has_job(username, token):
        print "new job: ", username, token
        cfg = set_config(form)
        task = auto_play.delay(cfg, token)
        redis.msetnx({
            userHash(username): tokenHash(username, token),
            tokenHash(username, token): task.id
            })
        status_url = request.url_root + 'status/%s' % task.id
        return flask.jsonify({
            'status_url': status_url,
            'task_id': task.id
            })

def sessionInfo():
    print "get session info: " + str(session)
    return session['username'], session['token']

def taskLog(taskId):
    logId = taskId + ' log'
    log = redis.lrange(logId, 0, 90)
    redis.ltrim(logId, 100, -1)
    return log

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
            status = taskLog(task_id)
        except Exception as e:
            status = str(e)
    else:
        status = 'Job not found'
    return flask.jsonify({'status': status})

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
