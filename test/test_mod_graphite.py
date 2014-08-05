#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (C) 2009-2010:
# Sébastien Coavoux, sebastien.coavoux@savoirfairelinux.com
# Philippe Pepos Petitclerc, philippe.pepos-petitclerc@savoirfairelinux.com
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
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Shinken. If not, see <http://www.gnu.org/licenses/>.


import socket
import struct
import cPickle
from shinken_test import *


#define_modules_dir("../modules")
modulesctx.set_modulesdir(modules_dir)

modgraphite_broker = modulesctx.get_module('graphite_perfdata')
Graphite_broker = modgraphite_broker.Graphite_broker

class TestModGraphite(ShinkenTest):
    def do_load_modules(self):
        self.modules_manager.load_and_init()
        self.log.log("I correctly loaded the modules: [%s]" %
                     (','.join([inst.get_name() for inst in self.modules_manager.instances])))

    def update_broker(self, dodeepcopy=False):
        # The brok should be manage in the good order
        ids = self.sched.brokers['Default-Broker']['broks'].keys()
        ids.sort()
        for brok_id in ids:
            brok = self.sched.brokers['Default-Broker']['broks'][brok_id]
            #print "Managing a brok type", brok.type, "of id", brok_id
            #if brok.type == 'update_service_status':
            #    print "Problem?", brok.data['is_problem']
            if dodeepcopy:
                brok = copy.deepcopy(brok)
            manage = getattr(self.graphite_broker, 'manage_' + brok.type + '_brok', None)
            if manage:
            # Be sure the brok is prepared before call it
                brok.prepare()
                manage(brok)
        self.sched.brokers['Default-Broker']['broks'] = {}

    def setUp(self):
        modconf = Module(
            {'module_name': 'Graphite-Perfdata',
             'module_type': 'graphite_perfdata',
             'port': '12345',
             'host': '127.0.0.1',
             'use_pickle': '1',
             'tick_limit': '300', })
        self.setup_with_file('etc/shinken_1r_1h_1s.cfg')
        self.graphite_broker = Graphite_broker(modconf)
        print "Cleaning old broks?"
        self.sched.conf.skip_initial_broks = False
        self.sched.brokers['Default-Broker'] = {'broks': {}, 'has_full_broks': False}
        self.sched.fill_initial_broks('Default-Broker')
        self.update_broker()
        self.nagios_path = None
        self.livestatus_path = None
        self.nagios_config = None
        # add use_aggressive_host_checking so we can mix exit codes 1 and 2
        # but still get DOWN state
        host = self.sched.hosts.find_by_name("test_host_0")
        host.__class__.use_aggressive_host_checking = 1
        self.sock_serv = socket.socket()
        self.sock_serv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_serv.bind(("127.0.0.1", 12345))
        self.sock_serv.listen(1)
        self.graphite_broker.init()
        self.conn_serv, _ = self.sock_serv.accept()


    def tearDown(self):
        if os.path.exists('var/shinken.log'):
            os.remove('var/shinken.log')
        if os.path.exists('var/retention.dat'):
            os.remove('var/retention.dat')
        if os.path.exists('var/status.dat'):
            os.remove('var/status.dat')
        self.conn_serv.close()
        self.sock_serv.close()


    def test_big_chunks(self):
        self.print_header()

        host = self.sched.hosts.find_by_name("test_host_0")
        host.checks_in_progress = []
        host.act_depend_of = []  # ignore the router
        svc = self.sched.services.find_srv_by_name_and_hostname("test_host_0", "test_ok_0")

        # To make tests quicker we make notifications send very quickly
        svc.notification_interval = 0.001

        svc.checks_in_progress = []
        svc.act_depend_of = []  # no hostchecks on critical checkresults

        # Get the Pending => UP lines
        self.scheduler_loop(1, [[host, 0, 'UP']], do_sleep=True, sleep_time=0.1)
        self.scheduler_loop(1, [[svc, 0, 'OK | time=1s;3;4;5;6']], do_sleep=True, sleep_time=0.1)
        self.scheduler_loop(1, [[svc, 0, 'OK | val=1k;4;5;6;7']], do_sleep=True, sleep_time=0.1)
        self.update_broker()
        self.graphite_broker.chunk_size = 1
        self.graphite_broker.hook_tick("DUMMY")
        output = self.conn_serv.recv(2048)
        data = []
        nb_packet = 0
        while len(output) > 0:
            sizep = struct.unpack("!L", output[:4])[0]
            data.append(cPickle.loads(output[4:4+sizep]))
            output = output[4+sizep:]
        self.assert_(len(data) == 6)

if __name__ == '__main__':
    unittest.main()