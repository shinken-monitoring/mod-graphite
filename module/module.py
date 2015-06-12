#!/usr/bin/python

# -*- coding: utf-8 -*-

# Copyright (C) 2009-2012:
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
#    Gregory Starck, g.starck@gmail.com
#    Hartmut Goebel, h.goebel@goebel-consult.de
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

import re
from re import compile

from socket import socket
import struct

from shinken.basemodule import BaseModule
from shinken.log import logger
from shinken.misc.perfdata import PerfDatas

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


# Class for the Graphite Broker
# Get broks and send them to a Carbon instance of Graphite
class Graphite_broker(BaseModule):
    def __init__(self, modconf):
        BaseModule.__init__(self, modconf)
        
        self.hosts_cache = {}
        self.services_cache = {}
        self.host_dict = {}
        self.svc_dict = {}
        
        self.con = None
        
        # Separate perfdata multiple values
        self.multival = re.compile(r'_(\d+)$')
        
        # Specific filter to allow metrics to include '.' for Graphite
        self.illegal_char_metric = re.compile(r'[^a-zA-Z0-9_.\-]')
        
        self.host = getattr(modconf, 'host', 'localhost')
        self.port = int(getattr(modconf, 'port', '2003'))
        logger.info("[Graphite] Configuration - host/port: %s:%d", self.host, self.port)
        
        # Used to reset check time into the scheduled time. 
        # Carbon/graphite does not like latency data and creates blanks in graphs
        # Every data with "small" latency will be considered create at scheduled time
        self.ignore_latency_limit = int(getattr(modconf, 'ignore_latency_limit', '0'))
        if self.ignore_latency_limit < 0:
            self.ignore_latency_limit = 0
        
        # service name to use for host check
        self.hostcheck = getattr(modconf, 'hostcheck', None)

        # optional "sub-folder" in graphite to hold the data of a specific host
        self.graphite_data_source = self.illegal_char_metric.sub('_', getattr(modconf, 'graphite_data_source', ''))
        logger.info("[Graphite] Configuration - Graphite data source: %s", self.graphite_data_source)

        # optional perfdatas to be filtered
        self.filtered_metrics = {}
        for filter in getattr(modconf, 'filter', '[]'):
            filtered_service, filtered_metric = filter.split(':')
            if filtered_service not in self.filtered_metrics:
                self.filtered_metrics[filtered_service] = []
            self.filtered_metrics[filtered_service].append(filtered_metric.split(','))
        
        for service in self.filtered_metrics:
            logger.info("[Graphite] Configuration - Filtered metric: %s - %s", service, self.filtered_metrics[service])

        # Send warning, critical, min, max
        self.send_warning = bool(getattr(modconf, 'send_warning', False))
        logger.info("[Graphite] Configuration - send warning metrics: %d", self.send_warning)
        self.send_critical = bool(getattr(modconf, 'send_critical', False))
        logger.info("[Graphite] Configuration - send critical metrics: %d", self.send_critical)
        self.send_min = bool(getattr(modconf, 'send_min', False))
        logger.info("[Graphite] Configuration - send min metrics: %d", self.send_min)
        self.send_max = bool(getattr(modconf, 'send_max', False))
        logger.info("[Graphite] Configuration - send max metrics: %d", self.send_max)

    # Called by Broker so we can do init stuff
    def init(self):
        logger.info("[Graphite] initializing connection to %s:%d ...", str(self.host), self.port)
        try:
            self.con = socket()
            self.con.connect((self.host, self.port))
        except IOError, err:
            logger.error("[Graphite] Graphite Carbon instance connexion failed"
                         " IOError: %s", str(err))
            # do not raise an exception - logging is enough ...
        else:
            logger.info("[Graphite] Connection successful to %s:%d", str(self.host), self.port)

    # Sending data to Carbon. In case of failure, try to reconnect and send again.
    def send_packet(self, packet):
        try:
            self.con.sendall(packet)
        except AttributeError:
            logger.error("[Graphite] Connexion to the Graphite Carbon instance is not available!"
                         " Trying to reconnect ... ")
            try:
                self.init()
                self.con.sendall(packet)
            except:
                logger.error("[Graphite] Failed sending, data are lost: \n%s", packet)
        except IOError:
            logger.error("[Graphite] Failed sending data to the Graphite Carbon instance !"
                         " Trying to reconnect ... ")
            try:
                self.init()
                self.con.sendall(packet)
            except:
                logger.error("[Graphite] Failed sending, data are lost: \n%s", packet)
        else:
            logger.debug("[Graphite] Data sent to Carbon: \n%s", packet)

    # For a perf_data like /=30MB;4899;4568;1234;0  /var=50MB;4899;4568;1234;0 /toto=
    # return ('/', '30'), ('/var', '50')
    def get_metric_and_value(self, service, perf_data):
        result = []
        metrics = PerfDatas(perf_data)

        for e in metrics:
            if service in self.filtered_metrics:
                if e.name in self.filtered_metrics[service]:
                    logger.warning("[Graphite] Ignore metric '%s' for filtered service: %s", e.name, service)
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


    # Prepare service cache
    def manage_initial_service_status_brok(self, b):
        host_name = b.data['host_name']
        service_description = b.data['service_description']
        service_id = host_name+"/"+service_description
        logger.debug("[Graphite] initial service status: %s", service_id)
        
        if not host_name in self.hosts_cache:
            logger.error("[Graphite] initial service status, host is unknown: %s.", host_name)
            return

        self.services_cache[service_id] = {}
        if '_GRAPHITE_POST' in b.data['customs']:
            self.services_cache[service_id]['_GRAPHITE_POST'] = b.data['customs']['_GRAPHITE_POST']
            
        logger.debug("[Graphite] initial service status received: %s", service_id)


    # Prepare host cache
    def manage_initial_host_status_brok(self, b):
        host_name = b.data['host_name']
        logger.debug("[Graphite] initial host status: %s", host_name)

        self.hosts_cache[host_name] = {}
        if '_GRAPHITE_PRE' in b.data['customs']:
            self.hosts_cache[host_name]['_GRAPHITE_PRE'] = b.data['customs']['_GRAPHITE_PRE']
            
        logger.debug("[Graphite] initial host status received: %s", host_name)


    # A service check result brok has just arrived ...
    def manage_service_check_result_brok(self, b):
        host_name = b.data['host_name']
        service_description = b.data['service_description']
        service_id = host_name+"/"+service_description
        logger.debug("[Graphite] service check result: %s", service_id)
        
        # If host/service initial status brok has not been received, ignore ...
        if host_name not in self.hosts_cache:
            logger.warning("[Graphite] received service check result for an unknown host: %s", host_name)
            return
        if service_id not in self.services_cache:
            logger.warning("[Graphite] received service check result for an unknown service: %s", service_id)
            return
                
        # Decode received metrics
        couples = self.get_metric_and_value(service_description, b.data['perf_data'])

        # If no values, we can exit now
        if len(couples) == 0:
            return

        hname = self.illegal_char.sub('_', host_name)
        if '_GRAPHITE_PRE' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_PRE'], hname))

        desc = self.illegal_char.sub('_', service_description)
        if '_GRAPHITE_POST' in self.services_cache[service_id]:
            desc = ".".join((desc, self.services_cache[service_id]['_GRAPHITE_POST']))

        if self.ignore_latency_limit >= b.data['latency'] > 0:
            check_time = int(b.data['last_chk']) - int(b.data['latency'])
            logger.info("[Graphite] Ignoring latency for service %s. Latency : %s",
                b.data['service_description'], b.data['latency'])
        else:
            check_time = int(b.data['last_chk']) 


        if self.graphite_data_source:
            path = '.'.join((hname, self.graphite_data_source, desc))
        else:
            path = '.'.join((hname, desc))

        lines = []
        # Send a bulk of all metrics at once
        for (metric, value) in couples:
            lines.append("%s.%s %s %d" % (path, metric, str(value), check_time))
        packet = '\n'.join(lines)

        self.send_packet(packet)


    # A host check result brok has just arrived, we UPDATE data info with this
    def manage_host_check_result_brok(self, b):
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
            return

        hname = self.illegal_char.sub('_', host_name)
        if '_GRAPHITE_PRE' in self.hosts_cache[host_name]:
            hname = ".".join((self.hosts_cache[host_name]['_GRAPHITE_PRE'], hname))

        if self.ignore_latency_limit >= b.data['latency'] > 0:
            check_time = int(b.data['last_chk']) - int(b.data['latency'])
            logger.info("[Graphite] Ignoring latency for service %s. Latency : %s",
                b.data['service_description'], b.data['latency'])
        else:
            check_time = int(b.data['last_chk']) 

        if self.graphite_data_source:
            path = '.'.join((hname, self.graphite_data_source))
        else:
            path = hname

        lines = []
        # Send a bulk of all metrics at once
        for (metric, value) in couples:
            if self.hostcheck:
                lines.append("%s.%s.%s %s %d" % (path, self.hostcheck, metric, value, check_time))
            else:
                lines.append("%s.%s %s %d" % (path, metric, value, check_time))
        packet = '\n'.join(lines)

        self.send_packet(packet)


    def main(self):
        self.set_proctitle(self.name)
        self.set_exit_handler()
        while not self.interrupted:
            l = self.to_q.get()
            for b in l:
                b.prepare()
                self.manage_brok(b)
