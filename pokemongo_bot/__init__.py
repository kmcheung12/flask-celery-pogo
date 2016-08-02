# -*- coding: utf-8 -*-

import logging
import random
import datetime
import re
from pgoapi import PGoApi
from cell_workers import PokemonCatchWorker, SeenFortWorker, MoveToFortWorker, InitialTransferWorker, EvolveAllWorker
from cell_workers.utils import distance
from human_behaviour import sleep
from stepper import Stepper
from geopy.geocoders import GoogleV3
from math import radians, sqrt, sin, cos, atan2
from item_list import Item


class PokemonGoBot(object):
    def __init__(self, config):
        self.config = config
        self.pokemon_list = config.pokemons
        self.item_list = config.items

    def start(self):
        self._setup_api()
        self.stepper = Stepper(self)
        random.seed()

    def setup_logging(self, logger):
        self._setup_logging()
        self.logger = logger
    
    def take_step(self):
        self.stepper.take_step()

    def work_on_cell(self, cell, position, include_fort_on_path):
        if self.config.evolve_all:
            # Run evolve all once. Flip the bit.
            print('[#] Attempting to evolve all pokemons ...')
            worker = EvolveAllWorker(self)
            worker.work()
            self.config.evolve_all = []

        self._filter_ignored_pokemons(cell)

        if (self.config.mode == "all" or self.config.mode ==
                "poke") and 'catchable_pokemons' in cell and len(cell[
                    'catchable_pokemons']) > 0:
            self.logger.info('[#] Something rustles nearby!')
            # Sort all by distance from current pos- eventually this should
            # build graph & A* it
            cell['catchable_pokemons'].sort(
                key=
                lambda x: distance(self.position[0], self.position[1], x['latitude'], x['longitude']))

        if (self.config.mode == "all" or self.config.mode == "poke"
            ) and 'wild_pokemons' in cell and len(cell['wild_pokemons']) > 0:
            # Sort all by distance from current pos- eventually this should
            # build graph & A* it
            cell['wild_pokemons'].sort(
                key=
                lambda x: distance(self.position[0], self.position[1], x['latitude'], x['longitude']))
            for pokemon in cell['wild_pokemons']:
                if self.catch_pokemon(pokemon) == PokemonCatchWorker.NO_POKEBALLS:
                    break
        if (self.config.mode == "all" or
                self.config.mode == "farm") and include_fort_on_path:
            if 'forts' in cell:
                # Only include those with a lat/long
                forts = [fort
                         for fort in cell['forts']
                         if 'latitude' in fort and 'type' in fort]
                gyms = [gym for gym in cell['forts'] if 'gym_points' in gym]

                # Sort all by distance from current pos- eventually this should
                # build graph & A* it
                forts.sort(key=lambda x: distance(self.position[
                           0], self.position[1], x['latitude'], x['longitude']))
                for fort in forts:
                    worker = MoveToFortWorker(fort, self)
                    worker.work()

                    worker = SeenFortWorker(fort, self)
                    hack_chain = worker.work()
                    if hack_chain > 10:
                        #print('need a rest')
                        break

    def _setup_logging(self):
        # log settings
        # log format
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s [%(module)10s] [%(levelname)5s] %(message)s')

        if self.config.debug:
            logging.getLogger("requests").setLevel(logging.DEBUG)
            logging.getLogger("pgoapi").setLevel(logging.DEBUG)
            logging.getLogger("rpc_api").setLevel(logging.DEBUG)
        else:
            logging.getLogger("requests").setLevel(logging.ERROR)
            logging.getLogger("pgoapi").setLevel(logging.ERROR)
            logging.getLogger("rpc_api").setLevel(logging.ERROR)

    def _setup_api(self):
        # instantiate pgoapi
        self.api = PGoApi()

        # provide player position on the earth
        self._set_starting_position()

        if not self.api.login(self.config.auth_service,
                              str(self.config.username),
                              str(self.config.password)):
            self.logger.error('Login Error, server busy', 'red')
            exit(0)

        # chain subrequests (methods) into one RPC call

        # get player profile call
        # ----------------------
        self.api.get_player()

        response_dict = self.api.call()
        currency_1 = "0"
        currency_2 = "0"

        player = response_dict['responses']['GET_PLAYER']['player_data']
        print response_dict

        # @@@ TODO: Convert this to d/m/Y H:M:S
        creation_date = datetime.datetime.fromtimestamp(
            player['creation_timestamp_ms'] / 1e3)

        pokecoins = '0'
        stardust = '0'
        balls_stock = self.pokeball_inventory()

        if 'amount' in player['currencies'][0]:
            pokecoins = player['currencies'][0]['amount']
        if 'amount' in player['currencies'][1]:
            stardust = player['currencies'][1]['amount']

        self.logger.info('[#] Username: {username}'.format(**player))
        self.logger.info('[#] Acccount Creation: {}'.format(creation_date))
        self.logger.info('[#] Bag Storage: {}/{}'.format(
            self.get_inventory_count('item'), player['max_item_storage']))
        self.logger.info('[#] Pokemon Storage: {}/{}'.format(
            self.get_inventory_count('pokemon'), player[
                'max_pokemon_storage']))
        self.logger.info('[#] Stardust: {}'.format(stardust))
        self.logger.info('[#] Pokecoins: {}'.format(pokecoins))
        self.logger.info('[#] PokeBalls: ' + str(balls_stock[1]))
        self.logger.info('[#] GreatBalls: ' + str(balls_stock[2]))
        self.logger.info('[#] UltraBalls: ' + str(balls_stock[3]))

        self.get_player_info()

        if self.config.initial_transfer:
            worker = InitialTransferWorker(self)
            worker.work()

        self.logger.info('[#]')
        self.update_inventory()

    def catch_pokemon(self, pokemon):
        worker = PokemonCatchWorker(pokemon, self)
        return_value = worker.work()

        if return_value == PokemonCatchWorker.BAG_FULL:
            worker = InitialTransferWorker(self)
            worker.work()

        return return_value

    def drop_item(self, item_id, count):
        self.api.recycle_inventory_item(item_id=item_id, count=count)
        inventory_req = self.api.call()

        # Example of good request response
        #{'responses': {'RECYCLE_INVENTORY_ITEM': {'result': 1, 'new_count': 46}}, 'status_code': 1, 'auth_ticket': {'expire_timestamp_ms': 1469306228058L, 'start': '/HycFyfrT4t2yB2Ij+yoi+on778aymMgxY6RQgvrGAfQlNzRuIjpcnDd5dAxmfoTqDQrbz1m2dGqAIhJ+eFapg==', 'end': 'f5NOZ95a843tgzprJo4W7Q=='}, 'request_id': 8145806132888207460L}
        return inventory_req

    def update_inventory(self):
        self.api.get_inventory()
        response = self.api.call()
        self.inventory = list()
        if 'responses' in response:
            if 'GET_INVENTORY' in response['responses']:
                if 'inventory_delta' in response['responses']['GET_INVENTORY']:
                    if 'inventory_items' in response['responses'][
                            'GET_INVENTORY']['inventory_delta']:
                        for item in response['responses']['GET_INVENTORY'][
                                'inventory_delta']['inventory_items']:
                            if not 'inventory_item_data' in item:
                                continue
                            if not 'item' in item['inventory_item_data']:
                                continue
                            if not 'item_id' in item['inventory_item_data'][
                                    'item']:
                                continue
                            if not 'count' in item['inventory_item_data'][
                                    'item']:
                                continue
                            self.inventory.append(item['inventory_item_data'][
                                'item'])

    def pokeball_inventory(self):
        self.api.get_player().get_inventory()

        inventory_req = self.api.call()
        inventory_dict = inventory_req['responses']['GET_INVENTORY'][
            'inventory_delta']['inventory_items']

        # get player balls stock
        # ----------------------
        balls_stock = {1: 0, 2: 0, 3: 0, 4: 0}

        for item in inventory_dict:
            try:
                # print(item['inventory_item_data']['item'])
                item_id = item['inventory_item_data']['item']['item_id']
                item_count = item['inventory_item_data']['item']['count']

                if item_id == Item.ITEM_POKE_BALL.value:
                    # print('Poke Ball count: ' + str(item_count))
                    balls_stock[1] = item_count
                if item_id == Item.ITEM_GREAT_BALL.value:
                    # print('Great Ball count: ' + str(item_count))
                    balls_stock[2] = item_count
                if item_id == Item.ITEM_ULTRA_BALL.value:
                    # print('Ultra Ball count: ' + str(item_count))
                    balls_stock[3] = item_count
            except:
                continue
        return balls_stock

    def item_inventory_count(self, id):
        self.api.get_player().get_inventory()

        inventory_req = self.api.call()
        inventory_dict = inventory_req['responses'][
            'GET_INVENTORY']['inventory_delta']['inventory_items']

        item_count = 0

        for item in inventory_dict:
            try:
                if item['inventory_item_data']['item']['item_id'] == int(id):
                    item_count = item[
                        'inventory_item_data']['item']['count']
            except:
                continue
        return item_count

    def _set_starting_position(self):

        if self.config.test:
            # TODO: Add unit tests
            return

        if self.config.location:
            try:
                location_str = str(self.config.location)
                location = (self._get_pos_by_name(location_str.replace(" ", "")))
                self.position = location
                self.api.set_position(*self.position)
                self.logger.info('')
                self.logger.info(u'[x] Address found: {}'.format(self.config.location.decode(
                    'utf-8')))
                self.logger.info('[x] Position in-game set as: {}'.format(self.position))
                self.logger.info('')
                return
            except:
                self.logger.info('[x] The location given using -l could not be parsed.')
                pass
        else:
            raise ValueError("No usable location")

    def _get_pos_by_name(self, location_name):
        # Check if the given location is already a coordinate.
        if ',' in location_name:
            possibleCoordinates = re.findall("[-]?\d{1,3}[.]\d{6,7}", location_name)
            if len(possibleCoordinates) == 2:
                # 2 matches, this must be a coordinate. We'll bypass the Google geocode so we keep the exact location.
                self.logger.info(
                    '[x] Coordinates found in passed in location, not geocoding.')
                return (float(possibleCoordinates[0]), float(possibleCoordinates[1]), float("0.0"))
            else:
                raise ValueError("Invalid GPS")
        else:
            raise ValueError("Invalid GPS")

    def _filter_ignored_pokemons(self, cell):
        process_ignore = False
        ignores = self.config.ignores
        if len(ignores) > 0:
            process_ignore = True

        if process_ignore:
            # remove any wild pokemon
            try:
                for p in cell['wild_pokemons'][:]:
                    pokemon_id = p['pokemon_data']['pokemon_id']
                    pokemon_name = filter(
                        lambda x: int(x.get('Number')) == pokemon_id,
                        self.pokemon_list)[0]['Name']

                    if pokemon_name in ignores:
                        cell['wild_pokemons'].remove(p)
            except KeyError:
                pass

            #
            # remove catchable pokemon
            try:
                for p in cell['catchable_pokemons'][:]:
                    pokemon_id = p['pokemon_id']
                    pokemon_name = filter(
                        lambda x: int(x.get('Number')) == pokemon_id,
                        self.pokemon_list)[0]['Name']

                    if pokemon_name in ignores:
                        cell['catchable_pokemons'].remove(p)
            except KeyError:
                pass

    def heartbeat(self):
        self.api.get_player()
        self.api.get_hatched_eggs()
        self.api.get_inventory()
        self.api.check_awarded_badges()
        self.api.call()

    def get_inventory_count(self, what):
        self.api.get_inventory()
        response_dict = self.api.call()
        if 'responses' in response_dict:
            if 'GET_INVENTORY' in response_dict['responses']:
                if 'inventory_delta' in response_dict['responses'][
                        'GET_INVENTORY']:
                    if 'inventory_items' in response_dict['responses'][
                            'GET_INVENTORY']['inventory_delta']:
                        pokecount = 0
                        itemcount = 1
                        for item in response_dict['responses'][
                                'GET_INVENTORY']['inventory_delta'][
                                    'inventory_items']:
                            #print('item {}'.format(item))
                            if 'inventory_item_data' in item:
                                if 'pokemon_data' in item[
                                        'inventory_item_data']:
                                    pokecount = pokecount + 1
                                if 'item' in item['inventory_item_data']:
                                    if 'count' in item['inventory_item_data'][
                                            'item']:
                                        itemcount = itemcount + \
                                            item['inventory_item_data'][
                                                'item']['count']
        if 'pokemon' in what:
            return pokecount
        if 'item' in what:
            return itemcount
        return '0'

    def get_player_info(self):
        self.api.get_inventory()
        response_dict = self.api.call()
        if 'responses' in response_dict:
            if 'GET_INVENTORY' in response_dict['responses']:
                if 'inventory_delta' in response_dict['responses'][
                        'GET_INVENTORY']:
                    if 'inventory_items' in response_dict['responses'][
                            'GET_INVENTORY']['inventory_delta']:
                        pokecount = 0
                        itemcount = 1
                        for item in response_dict['responses'][
                                'GET_INVENTORY']['inventory_delta'][
                                    'inventory_items']:
                            #print('item {}'.format(item))
                            if 'inventory_item_data' in item:
                                if 'player_stats' in item[
                                        'inventory_item_data']:
                                    playerdata = item['inventory_item_data'][
                                        'player_stats']

                                    nextlvlxp = (
                                        int(playerdata.get('next_level_xp', 0)) -
                                        int(playerdata.get('experience', 0)))

                                    if 'level' in playerdata:
                                        self.logger.info(
                                            '[#] -- Level: {level}'.format(
                                                **playerdata))

                                    if 'experience' in playerdata:
                                        self.logger.info(
                                            '[#] -- Experience: {experience}'.format(
                                                **playerdata))
                                        self.logger.info(
                                            '[#] -- Experience until next level: {}'.format(
                                                nextlvlxp))

                                    if 'pokemons_captured' in playerdata:
                                        self.logger.info(
                                            '[#] -- Pokemon Captured: {pokemons_captured}'.format(
                                                **playerdata))

                                    if 'poke_stop_visits' in playerdata:
                                        self.logger.info(
                                            '[#] -- Pokestops Visited: {poke_stop_visits}'.format(
                                                **playerdata))
