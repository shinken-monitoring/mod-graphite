#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (C) 2009-2012:
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
#    Gregory Starck, g.starck@gmail.com
#    Hartmut Goebel, h.goebel@goebel-consult.de
#    Frederic Mohier, frederic.mohier@gmail.com
#
# This file is part of Shinken.
#
# Shinken is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Shinken is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Shinken.  If not, see <http://www.gnu.org/licenses/>.

"""This Class is a plugin for the Shinken Broker. It is in charge
to brok information of the service/host perfdatas into the Graphite
backend. http://graphite.wikidot.com/start
"""

from re import compile
import time

from socket import socket
from collections import deque

from shinken.basemodule import BaseModule
from shinken.log import logger
from shinken.misc.perfdata import PerfDatas

db_available = False
try:
    import MySQLdb
    from MySQLdb import IntegrityError, ProgrammingError, OperationalError
    logger.info("[Graphite] database library is available")
    db_available = True
except ImportError:
    logger.error("[Graphite] Can not import MySQLdb, install with 'apt-get install python-mysqldb'")

properties = {
    'daemons': ['broker'],
    'type': 'graphite_perfdata',
    'external': True,
}


# Called by the plugin manager to get a broker
def get_instance(mod_conf):
    logger.info("[Graphite] Get a graphite data module for plugin %s" % mod_conf.get_name())
    instance = Graphite_broker(mod_conf)
    return instance

def stringify(self, val):
    """Get a unicode from a value"""
    # If raw string, go in unicode
    if isinstance(val, str):
        val = val.decode('utf8', 'ignore').replace("'", "''")
    elif isinstance(val, unicode):
        val = val.replace("'", "''")
    else:  # other type, we can str
        val = unicode(str(val))
        val = val.replace("'", "''")
    return val

# Class for the Graphite Broker
# Get broks and send them to a Carbon instance of Graphite
class Graphite_broker(BaseModule):

    def __init__(self, modconf):
        BaseModule.__init__(self, modconf)

        self.hosts_cache = {}
        self.services_cache = {}

        # Database storage configuration
        self.db = None
        self.db_host = getattr(modconf, 'db_host', None)
        self.db_port = int(getattr(modconf, 'db_port', '3306'))
        self.db_user = getattr(modconf, 'db_user', 'shinken')
        self.db_password = getattr(modconf, 'db_password', 'shinken')
        self.db_database = getattr(modconf, 'db_database', '')
        self.db_table = getattr(modconf, 'db_table', '')
        self.db_character_set = getattr(modconf, 'db_character_set', 'utf8')
        logger.info("[Graphite] Configuration - database host/port: %s:%d", self.db_host, self.db_port)
        # Perfdata query
        # Use \\ where as only one is necessary in SQL ...
        self.querySelectMetrics = """SELECT `kc`.`id`, `kc`.`counter_name`, `kc`.`regexp_perfdata`, IF(LOCATE("'", `kc`.`regexp_perfdata`)>0, SUBSTRING_INDEX(SUBSTRING(`kc`.`regexp_perfdata`, LOCATE("'", `kc`.`regexp_perfdata`)+1), "'", 1) , 'X') AS metric, `kc`.`counter_type`, `mc`.`name`, `mc`.`description` FROM `glpi_plugin_kiosks_counters` as kc, `glpi_plugin_monitoring_components` as mc WHERE `kc`.`plugin_monitoring_components_id` = `mc`.`id` AND `kc`.`is_active` = 1;"""
        # Insert query
        # CREATE TABLE `glpi_plugin_kiosks_metrics` (
        #   `id` int(11) NOT NULL AUTO_INCREMENT,
        #   `timestamp` int(11) DEFAULT '0',
        #   `hostname` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
        #   `service` varchar(255) COLLATE utf8_unicode_ci DEFAULT '',
        #   `counter` varchar(255) COLLATE utf8_unicode_ci DEFAULT NULL,
        #   `value` decimal(8,2) DEFAULT '0.00',
        #   `collected` tinyint(1) DEFAULT '0',
        #   PRIMARY KEY (`id`),
        #   KEY `timestamp` (`timestamp`)
        # ) ENGINE=MyISAM AUTO_INCREMENT=5 DEFAULT CHARSET=utf8 COLLATE=utf8_unicode_ci
        self.queryInsertCounter = "INSERT INTO `%s`.`%s` (`timestamp`,`hostname`,`service`,`counter`,`value`,`collected`) VALUES ('%s', '%s', '%s', '%s', '%s', '%s');"
        # Stored metrics
        self.db_stored_metrics = {}

        # Separate perfdata multiple values
        self.multival = compile(r'_(\d+)$')

        # Specific filter to allow metrics to include '.' for Graphite
        self.illegal_char_metric = compile(r'[^a-zA-Z0-9_.\-]')

        # Specific filter for host and services names for Graphite
        self.illegal_char_hostname = compile(r'[^a-zA-Z0-9_\-]')

        self.host = getattr(modconf, 'host', 'localhost')
        self.port = int(getattr(modconf, 'port', '2003'))
        logger.info("[Graphite] Configuration - host/port: %s:%d", self.host, self.port)

        # Connection and cache management
        self.con = None
        self.cache_max_length = int(getattr(modconf, 'cache_max_length', '1000'))
        logger.info('[Graphite] Configuration - maximum cache size: %d packets', self.cache_max_length)
        self.cache_commit_volume = int(getattr(modconf, 'cache_commit_volume', '100'))
        logger.info('[Graphite] Configuration - maximum cache commit volume: %d packets', self.cache_commit_volume)
        # Carbon and database caches
        self.cache = deque(maxlen=self.cache_max_length)
        self.db_cache = deque(maxlen=self.cache_max_length)

        # Used to reset check time into the scheduled time.
        # Carbon/graphite does not like latency data and creates blanks in graphs
        # Every data with "small" latency will be considered create at scheduled time
        self.ignore_latency_limit = int(getattr(modconf, 'ignore_latency_limit', '0'))
        if self.ignore_latency_limit < 0:
            self.ignore_latency_limit = 0

        # service name to use for host check
        self.hostcheck = getattr(modconf, 'hostcheck', '')

        # optional "sub-folder" in graphite to signal shinken data source
        self.graphite_data_source = self.illegal_char_metric.sub('_', getattr(modconf, 'graphite_data_source', ''))
        logger.info("[Graphite] Configuration - Graphite data source: %s", self.graphite_data_source)

        # optional perfdatas to be filtered
        self.filtered_metrics = {}
        filters = getattr(modconf, 'filter', [])
        if isinstance(filters, str) or isinstance(filters, unicode):
            filters = [filters]
        for filter in filters:
            try:
                filtered_service, filtered_metric = filter.split(':')
                self.filtered_metrics[filtered_service] = []
                if filtered_metric:
                    self.filtered_metrics[filtered_service] = filtered_metric.split(',')
            except:
                logger.warning("[Graphite] Configuration - ignoring badly declared filtered metric: %s", filter)
                pass

        for service in self.filtered_metrics:
            logger.info("[Graphite] Configuration - Filtered metrics: %s - %s", service, self.filtered_metrics[service])

        # Send warning, critical, min, max
        self.send_warning = bool(getattr(modconf, 'send_warning', False))
        logger.info("[Graphite] Configuration - send warning metrics: %d", self.send_warning)
        self.send_critical = bool(getattr(modconf, 'send_critical', False))
        logger.info("[Graphite] Configuration - send critical metrics: %d", self.send_critical)
        self.send_min = bool(getattr(modconf, 'send_min', False))
        logger.info("[Graphite] Configuration - send min metrics: %d", self.send_min)
        self.send_max = bool(getattr(modconf, 'send_max', False))
        logger.info("[Graphite] Configuration - send max metrics: %d", self.send_max)

    def init(self):
        """
        Called by Broker so we can do init stuff
        """
        if not self.con:
            logger.info("[Graphite] initializing Carbon connection to %s:%d ...", str(self.host), self.port)
            try:
                self.con = socket()
                self.con.connect((self.host, self.port))
            except IOError as e:
                logger.error("[Graphite] Graphite Carbon instance connexion failed"
                             " IOError: %s", str(e))
                # do not raise an exception - logging is enough ...
                self.con = None

        logger.info("[Graphite] Database connection: %d ...", db_available)
        if db_available and self.db_host:
            if not self.db:
                logger.info("[Graphite] Connecting to database: %s:%d (%s)", self.db_host, self.db_port, self.db_database)
                try:
                    self.db = MySQLdb.connect(host=self.db_host, user=self.db_user,
                                              passwd=self.db_password, db=self.db_database,
                                              port=self.db_port)
                    self.db.set_character_set(self.db_character_set)
                    self.db_cursor = self.db.cursor()
                    self.db_cursor.execute('SET NAMES %s;' % self.db_character_set)
                    self.db_cursor.execute('SET CHARACTER SET %s;' % self.db_character_set)
                    self.db_cursor.execute('SET character_set_connection=%s;' %
                                           self.db_character_set)
                    logger.info("[Graphite] Connected to database")
                except Exception as e:
                    logger.error("[Graphite] Graphite database connexion failed"
                                 " Error: %s", str(e))
                    # do not raise an exception - logging is enough ...
                    self.db = None
                    return

                try:
                    # Get metrics database configuration
                    self.db_cursor.execute(self.querySelectMetrics)
                    logger.debug("[Graphite] got database metrics filtering configuration")

                    for row in self.db_cursor.fetchall():
                        # row[0] : kiosks_counter.id
                        # row[1] : kiosks_counter.counter_name
                        # row[2] : kiosks_counter.regexp_perfdata
                        # row[3] : metric
                        # row[4] : kiosks_counter.counter_type
                        # row[5] : monitoring_components.name
                        # row[6] : monitoring_components.description
                        id = row[0]
                        name = row[1]
                        regex = row[2]
                        metric = row[3]
                        type = row[4]
                        alias = row[5]
                        service = row[6]
                        logger.info("[Graphite] database storable service/metric: %s (%s) / %s", service, alias, metric)
                        logger.info("[Graphite]  - %s - %s - %s - %s", id, name, regex, type)
                        # Build a dict organized as:
                        # 'nsca_reader': {
                        #    'Powered Cards': {'regex': "'Powered Cards'=(\\d+)c", 'alias': 'Lecteur de cartes', 'type': 'differential', 'id': 6L, 'name': 'cCardsInsertedOk'},
                        #    'Cards Removed': {'regex': "'Cards Removed'=(\\d+)c", 'alias': 'Lecteur de cartes', 'type': 'differential', 'id': 8L, 'name': 'cCardsRemoved'},
                        #    'Mute Cards': {'regex': "'Mute Cards'=(\\d+)c", 'alias': 'Lecteur de cartes', 'type': 'differential', 'id': 7L, 'name': 'cCardsInsertedKo'}
                        # }
                        if service not in self.db_stored_metrics:
                            self.db_stored_metrics[service] = {}
                        self.db_stored_metrics[service].update ({
                            metric: {
                                'id': id,
                                'name': name,
                                'regex': regex,
                                'type': type,
                                'alias': alias
                            }
                        })
                except IntegrityError as exp:
                    logger.warning("[Graphite] A query raised an integrity error: %s, %s", self.querySelectMetrics, exp)
                except ProgrammingError as exp:
                    logger.warning("[Graphite] A query raised a programming error: %s, %s", self.querySelectMetrics, exp)
                except Exception as exp:
                    logger.error("[Graphite] database error '%s' when executing query: %s", str(exp), self.querySelectMetrics)

            logger.info("[Graphite] database metrics: %s", self.db_stored_metrics)

    def send_packet(self, packet):
        """
        Sending data to Carbon. In case of failure, try to reconnect and send again.
        """
        if not self.con:
            self.init()

        if not self.con:
            logger.warning("[Graphite] Connection to the Graphite Carbon instance is broken!"
                           " Storing data in module cache ... ")
            self.cache.append(packet)
            logger.warning("[Graphite] cached metrics %d packets", len(self.cache))
            return False

        if self.cache:
            logger.info("[Graphite] %d cached metrics packet(s) to send to Graphite", len(self.cache))
            commit_count = 0
            now = time.time()
            while True:
                try:
                    self.con.sendall(self.cache.popleft())
                    commit_count = commit_count + 1
                    if commit_count >= self.cache_commit_volume:
                        break
                except IndexError:
                    logger.debug("[Graphite] sent all cached metrics")
                    break
                except Exception, exp:
                    logger.error("[Graphite] exception: %s", str(exp))
            logger.info("[Graphite] time to flush %d cached metrics packet(s) (%2.4f)", commit_count, time.time() - now)

        try:
            self.con.sendall(packet)
            logger.debug("[Graphite] Data sent to Carbon: \n%s", packet)
        except IOError:
            logger.warning("[Graphite] Failed sending data to the Graphite Carbon instance !"
                           " Storing data in module cache ... ")
            self.cache.append(packet)
            logger.warning("[Graphite] cached metrics %d packets", len(self.cache))
            return False

        return True

    def db_store(self, records):
        """
        Storing data in database
        """
        if not db_available:
            return

        if not self.db:
            self.init()

        if not self.db:
            logger.warning("[Graphite] Connection to the database instance is broken!"
                           " Storing data in module cache ... ")
            self.db_cache.append(records)
            logger.warning("[Graphite] cached metrics %d records", len(self.db_cache))
            return False

        if self.db_cache:
            logger.info("[Graphite] %d cached metrics packet(s) to send to Graphite", len(self.db_cache))
            commit_count = 0
            now = time.time()
            while True:
                for query in self.db_cache.popleft():
                    try:
                        self.db_cursor.execute(query)
                        self.db.commit()

                        commit_count = commit_count + 1
                        if commit_count >= self.cache_commit_volume:
                            break
                    except IndexError:
                        logger.debug("[Graphite] executed all cached queries")
                        break
                    except IntegrityError as exp:
                        logger.warning("[Graphite] A query raised an integrity error: %s, %s", query, exp)
                        continue
                    except ProgrammingError as exp:
                        logger.warning("[Graphite] A query raised a programming error: %s, %s", query, exp)
                        continue
                    except Exception as exp:
                        logger.error("[Graphite] database error '%s' when executing query: %s", str(exp), query)

                logger.info("[Graphite] time to flush %d cached metrics record(s) (%2.4f)", commit_count, time.time() - now)

        for query in records:
            try:
                self.db_cursor.execute(query)
                self.db.commit()
                logger.info("[Graphite] executed query: %s", query)
            except IntegrityError as exp:
                logger.warning("[Graphite] A query raised an integrity error: %s, %s", query, exp)
                break
            except ProgrammingError as exp:
                logger.warning("[Graphite] A query raised a programming error: %s, %s", query, exp)
                break
            except Exception as exp:
                logger.error("[Graphite] database error '%s' when executing query: %s", str(exp), query)

        return True

    def get_metric_and_value(self, service, perf_data):
        """
        Extract metrics from performance data
        """
        result = []
        metrics = PerfDatas(perf_data)

        for e in metrics:
            logger.debug("[Graphite] service: %s, metric: %s", e.name)
            if service in self.filtered_metrics:
                if e.name in self.filtered_metrics[service]:
                    logger.debug("[Graphite] Ignore metric '%s' for filtered service: %s", e.name, service)
                    continue

            name = self.illegal_char_metric.sub('_', e.name)
            name = self.multival.sub(r'.\1', name)

            # get metric value and its thresholds values if they exist
            name_value = {name: e.value}
            # bailout if no value
            if name_value[name] == '':
                continue

            # Get or ignore extra values depending upon module configuration
            if e.warning and self.send_warning:
                name_value[name + '_warn'] = e.warning

            if e.critical and self.send_critical:
                name_value[name + '_crit'] = e.critical

            if e.min and self.send_min:
                name_value[name + '_min'] = e.min

            if e.max and self.send_max:
                name_value[name + '_max'] = e.max

            for key, value in name_value.items():
                result.append((key, value))

        return result

    def manage_initial_service_status_brok(self, b):
        """
        Prepare module services cache
        """
        host_name = b.data['host_name']
        service_description = b.data['service_description']
        service_id = host_name+"/"+service_description
        logger.info("[Graphite] got initial service status: %s", service_id)

        if not host_name in self.hosts_cache:
            logger.error("[Graphite] initial service status, host is unknown: %s.", service_id)
            return

        self.services_cache[service_id] = {}
        if '_GRAPHITE_POST' in b.data['customs']:
            self.services_cache[service_id]['_GRAPHITE_POST'] = b.data['customs']['_GRAPHITE_POST']

        logger.debug("[Graphite] initial service status received: %s", service_id)

    def manage_initial_host_status_brok(self, b):
        """
        Prepare module hosts cache
        """
        host_name = b.data['host_name']
        logger.info("[Graphite] got initial host status: %s", host_name)

        self.hosts_cache[host_name] = {}
        if '_GRAPHITE_PRE' in b.data['customs']:
            self.hosts_cache[host_name]['_GRAPHITE_PRE'] = b.data['customs']['_GRAPHITE_PRE']
        if '_GRAPHITE_GROUP' in b.data['customs']:
            self.hosts_cache[host_name]['_GRAPHITE_GROUP'] = b.data['customs']['_GRAPHITE_GROUP']

        logger.debug("[Graphite] initial host status received: %s", host_name)

    def manage_service_check_result_brok(self, b):
        """
        A service check result brok has just arrived ...
        """
        host_name = b.data['host_name']
        service_description = b.data['service_description']
        service_id = host_name+"/"+service_description
        logger.debug("[Graphite] service check result: %s", service_id)

        # If host and service initial status brokes have not been received, ignore ...
        if host_name not in self.hosts_cache:
            logger.warning("[Graphite] received service check result for an unknown host: %s", service_id)
            return
        if service_id not in self.services_cache:
            logger.warning("[Graphite] received service check result for an unknown service: %s", service_id)
            return

        if service_description in self.filtered_metrics:
            if len(self.filtered_metrics[service_description]) == 0:
                logger.info("[Graphite] Ignore service '%s' metrics", service_description)
                return

        # Decode received metrics
        couples = self.get_metric_and_value(service_description, b.data['perf_data'])

        # If no values, we can exit now
        if len(couples) == 0:
            logger.debug("[Graphite] no metrics to send ...")
            return

        # Custom hosts variables
        hname = self.illegal_char_hostname.sub('_', host_name)
        if '_GRAPHITE_GROUP' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_GROUP'], hname))

        if '_GRAPHITE_PRE' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_PRE'], hname))

        # Custom services variables
        desc = self.illegal_char_hostname.sub('_', service_description)
        if '_GRAPHITE_POST' in self.services_cache[service_id]:
            desc = ".".join((desc, self.services_cache[service_id]['_GRAPHITE_POST']))

        # Checks latency
        if self.ignore_latency_limit >= b.data['latency'] > 0:
            check_time = int(b.data['last_chk']) - int(b.data['latency'])
            logger.info("[Graphite] Ignoring latency for service %s. Latency : %s",
                b.data['service_description'], b.data['latency'])
        else:
            check_time = int(b.data['last_chk'])

        # Graphite data source
        if self.graphite_data_source:
            path = '.'.join((hname, self.graphite_data_source, desc))
        else:
            path = '.'.join((hname, desc))

        lines = []
        # Send a bulk of all metrics at once
        for (metric, value) in couples:
            lines.append("%s.%s %s %d" % (path, metric, str(value), check_time))
        lines.append("\n")
        packet = '\n'.join(lines)
        self.send_packet(packet)

        if self.db_host:
            # Interested to store service information in DB?
            if service_description in self.db_stored_metrics:
                logger.debug("[Graphite] check if anything in DB for service: %s", service_description)
                records = []
                for (metric, value) in couples:
                    # Interested in this metric?
                    if metric in self.db_stored_metrics[service_description]:
                        # Append query to records list
                        records.append(self.queryInsertCounter % (
                            self.db_database, self.db_table,
                            check_time, host_name, service_description, metric, value, 0
                        ))
                    else:
                        logger.debug("[Graphite] do not store anything in DB for metric: %s", metric)

                if records:
                    self.db_store(records)
            else:
                logger.debug("[Graphite] do not store anything in DB for service: %s", service_description)

    def manage_host_check_result_brok(self, b):
        """
        A host check result brok has just arrived, we UPDATE data info with this
        """
        host_name = b.data['host_name']
        logger.debug("[Graphite] host check result: %s", host_name)

        # If host initial status brok has not been received, ignore ...
        if host_name not in self.hosts_cache:
            logger.warning("[Graphite] received service check result for an unknown host: %s", host_name)
            return

        # Decode received metrics
        couples = self.get_metric_and_value('host_check', b.data['perf_data'])

        # If no values, we can exit now
        if len(couples) == 0:
            logger.debug("[Graphite] no metrics to send ...")
            return

        # Custom hosts variables
        hname = self.illegal_char_hostname.sub('_', host_name)
        if '_GRAPHITE_GROUP' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_GROUP'], hname))

        if '_GRAPHITE_PRE' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_PRE'], hname))

        if self.hostcheck:
            hname = '.'.join((hname, self.hostcheck))

        # Checks latency
        if self.ignore_latency_limit >= b.data['latency'] > 0:
            check_time = int(b.data['last_chk']) - int(b.data['latency'])
            logger.info("[Graphite] Ignoring latency for service %s. Latency : %s",
                b.data['service_description'], b.data['latency'])
        else:
            check_time = int(b.data['last_chk'])

        # Graphite data source
        if self.graphite_data_source:
            path = '.'.join((hname, self.graphite_data_source))
        else:
            path = hname

        lines = []
        # Send a bulk of all metrics at once
        for (metric, value) in couples:
            lines.append("%s.%s %s %d" % (path, metric, value, check_time))
        lines.append("\n")
        packet = '\n'.join(lines)
        self.send_packet(packet)

        if self.db_host:
            # Interested to store service information in DB?
            if self.hostcheck in self.db_stored_metrics:
                logger.debug("[Graphite] check if anything in DB for service: %s", self.hostcheck)
                records = []
                for (metric, value) in couples:
                    # Interested in this metric?
                    if metric in self.db_stored_metrics[self.hostcheck]:
                        # Append query to records list
                        records.append(self.queryInsertCounter % (
                            self.db_database, self.db_table,
                            check_time, host_name, self.hostcheck, metric, value, 0
                        ))
                    else:
                        logger.debug("[Graphite] do not store anything in DB for metric: %s", metric)

                if records:
                    self.db_store(records)
            else:
                logger.debug("[Graphite] do not store anything in DB for service: %s", self.hostcheck)

    def main(self):
        """
        Module main function, get broks from process queue
        """
        self.set_proctitle(self.name)
        self.set_exit_handler()
        while not self.interrupted:
            logger.debug("[Graphite] queue length: %s", self.to_q.qsize())
            now = time.time()

            l = self.to_q.get()
            for b in l:
                b.prepare()
                self.manage_brok(b)

            logger.debug("[Graphite] time to manage %s broks (%3.4fs)", len(l), time.time() - now)
