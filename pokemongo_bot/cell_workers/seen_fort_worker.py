# -*- coding: utf-8 -*-

import json
import time
from math import radians, sqrt, sin, cos, atan2
from pgoapi.utilities import f2i, h2f
from utils import  format_time
from pokemongo_bot.human_behaviour import sleep


class SeenFortWorker(object):
    def __init__(self, fort, bot):
        self.fort = fort
        self.api = bot.api
        self.bot = bot
        self.position = bot.position
        self.config = bot.config
        self.item_list = bot.item_list
        self.rest_time = 50
        self.stepper = bot.stepper
        self.logger = bot.logger

    def work(self):
        lat = self.fort['latitude']
        lng = self.fort['longitude']

        self.api.fort_details(fort_id=self.fort['id'],
                              latitude=lat,
                              longitude=lng)
        response_dict = self.api.call()
        if 'responses' in response_dict \
                and'FORT_DETAILS' in response_dict['responses'] \
                and 'name' in response_dict['responses']['FORT_DETAILS']:
            fort_details = response_dict['responses']['FORT_DETAILS']
            fort_name = fort_details['name'].encode('utf8', 'replace')
        else:
            fort_name = 'Unknown'
        self.logger.info('[#] Now at Pokestop: ' + fort_name + ' - Spinning...')
        sleep(2)
        self.api.fort_search(fort_id=self.fort['id'],
                             fort_latitude=lat,
                             fort_longitude=lng,
                             player_latitude=f2i(self.position[0]),
                             player_longitude=f2i(self.position[1]))
        response_dict = self.api.call()
        if 'responses' in response_dict and \
                'FORT_SEARCH' in response_dict['responses']:

            spin_details = response_dict['responses']['FORT_SEARCH']
            if spin_details['result'] == 1:
                self.logger.info("[+] Loot: ")
                experience_awarded = spin_details.get('experience_awarded',
                                                      False)
                if experience_awarded:
                    self.logger.info("[+] " + str(experience_awarded) + " xp")

                items_awarded = spin_details.get('items_awarded', False)
                if items_awarded:
                    tmp_count_items = {}
                    for item in items_awarded:
                        item_id = item['item_id']
                        if not item_id in tmp_count_items:
                            tmp_count_items[item_id] = item['item_count']
                        else:
                            tmp_count_items[item_id] += item['item_count']

                    for item_id, item_count in tmp_count_items.iteritems():
                        item_name = self.item_list[str(item_id)]

                        self.logger.info("[+] " + str(item_count) +
                                    "x " + item_name +
                                    " (Total: " + str(self.bot.item_inventory_count(item_id)) + ")")

                        # RECYCLING UNWANTED ITEMS
                        if str(item_id) in self.config.item_filter:
                            self.logger.info("[+] Recycling " + str(item_count) + "x " + item_name + "...")
                            #RECYCLE_INVENTORY_ITEM
                            response_dict_recycle = self.bot.drop_item(item_id=item_id, count=item_count)

                            if response_dict_recycle and \
                                'responses' in response_dict_recycle and \
                                'RECYCLE_INVENTORY_ITEM' in response_dict_recycle['responses'] and \
                                    'result' in response_dict_recycle['responses']['RECYCLE_INVENTORY_ITEM']:
                                result = response_dict_recycle['responses']['RECYCLE_INVENTORY_ITEM']['result']
                            if result is 1: # Request success
                                self.logger.info("[+] Recycling success")
                            else:
                                self.logger.info("[+] Recycling failed!")
                else:
                    self.logger.info("[#] Nothing found.")

                pokestop_cooldown = spin_details.get(
                    'cooldown_complete_timestamp_ms')
                if pokestop_cooldown:
                    seconds_since_epoch = time.time()
                    self.logger.info('[#] PokeStop on cooldown. Time left: ' + str(
                        format_time((pokestop_cooldown / 1000) -
                                    seconds_since_epoch)))

                if not items_awarded and not experience_awarded and not pokestop_cooldown:
                    message = (
                        'Stopped at Pokestop and did not find experience, items '
                        'or information about the stop cooldown. You are '
                        'probably softbanned. Try to play on your phone, '
                        'if pokemons always ran away and you find nothing in '
                        'PokeStops you are indeed softbanned. Please try again '
                        'in a few hours.')
                    raise RuntimeError(message)
            elif spin_details['result'] == 2:
                self.logger.info("[#] Pokestop out of range")
            elif spin_details['result'] == 3:
                pokestop_cooldown = spin_details.get(
                    'cooldown_complete_timestamp_ms')
                if pokestop_cooldown:
                    seconds_since_epoch = time.time()
                    self.logger.info('[#] PokeStop on cooldown. Time left: ' + str(
                        format_time((pokestop_cooldown / 1000) -
                                    seconds_since_epoch)))
            elif spin_details['result'] == 4:
                self.logger.info("[#] Inventory is full, switching to catch mode...")
                self.config.mode = 'poke'

            if 'chain_hack_sequence_number' in response_dict['responses'][
                    'FORT_SEARCH']:
                time.sleep(2)
                return response_dict['responses']['FORT_SEARCH'][
                    'chain_hack_sequence_number']
            else:
                self.logger.info('[#] may search too often, lets have a rest')
                return 11
        sleep(7)
        return 0

    @staticmethod
    def closest_fort(current_lat, current_long, forts):
        print x
