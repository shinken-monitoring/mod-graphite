
Graphite2 module
=====================

Shinken module for exporting data to a Graphite server, version 2

This version is a refactoring of the previous graphite module which allows:

   - run as an external broker module
   - do not manage metrics until initial hosts/services status are received (avoid to miss prefixes)
   - remove pickle communication with Carbon (not very safe ...)
   - maintain a cache for the packets not sent because of connection problems
   - improve configuration features:
      - filter metrics warning and critical thresholds
      - filter metrics min and max values
      - filter service/metrics (avoid sending all metrics to Carbon)
      - manage host _GRAPHITE_PRE and service _GRAPHITE_POST to build metric id
      - manage host _GRAPHITE_GROUP as an extra hierarchy level for metrics (easier usage in metrics dashboard)

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

For example, this prefix may be the API key of an hosted Graphite account (http://hostedgraphite.com).

Use `_GRAPHITE_GROUP` in the host configuration to set a prefix to use after the prefix and before the host name.
You can set `_GRAPHITE_GROUP` in a specific host template to allow easier filtering and organization in the metrics of a dashboard.

For example, declare this custom variable in an hostgroup or an host template.

