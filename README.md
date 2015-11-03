<a href='https://travis-ci.org/shinken-monitoring/mod-graphite'><img src='https://api.travis-ci.org/shinken-monitoring/mod-graphite.svg?branch=master' alt='Travis Build'></a>
mod-graphite2
=============

Shinken module for exporting data to a Graphite server, version 2


Install
--------------------------------
```
   su - shinken
   shinken install graphite2
```

Configure
--------------------------------
```
   vi /etc/shinken/brokers/broker-master.cfg
   => modules graphite2

   vi /etc/shinken/modules/graphite2.cfg
   => host graphite
```

Run
--------------------------------
```
   su -
   /etc/init.d/shinken restart
```

Hosts specific configuration
--------------------------------
Use `_GRAPHITE_PRE` in the host configuration to set a prefix to use before the host name.
You can set `_GRAPHITE_PRE` in a global host template for all hosts.
For example, this prefix may be the API kay of an hosted Graphite account (http://hostedgraphite.com).

Use `_GRAPHITE_GROUP` in the host configuration to set a prefix to use after the prefix and before the host name.
You can set `_GRAPHITE_GROUP` in a specific host template to allow easier filtering and organization in the metrics of a dashboard.
For example, declare this custom variable in an hostgroup or an host template.

