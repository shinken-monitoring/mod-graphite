.. _graphite_module:

===========================
Graphite metrics
===========================

This module allows Shinken to send metrics to a Carbon/Graphite instance.

This version is a refactoring of the previous graphite module which allows:

   - run as an external broker module
   - do not manage metrics until initial hosts/services status are received (avoid to miss prefixes)
   - remove pickle communication with Carbon (not very safe ...)
   - improve configuration features:
      - filter metrics warning and critical thresholds
      - filter metrics min and max values
      - filter service/metrics (avoid sending all metrics to Carbon)
      - manage host _GRAPHITE_PRE and service _GRAPHITE_POST to build metric id
      - manage host _GRAPHITE_GROUP as an extra hierarchy level for metrics (easier usage in metrics dashboard)

Requirements
-------------------------

None.


Enabling module
-------------------------

To use the `graphite2` module you must declare it in your main broker configuration.

::

   define broker {
      ...

      modules  ..., graphite2

   }


Configuring module
-------------------------

The module configuration is defined in the file: ``graphite2.cfg``.

Default configuration file is as is :
```
   ## Module:      graphite
   ## Loaded by:   Broker
   # Export host and service performance data to Graphite carbon process.
   # Graphite is a time series database with a rich web service interface, viewed
   # as a modern alternative to RRDtool.
   define module {
      module_name     graphite2
      module_type     graphite_perfdata

      # Graphite server / port to use
      # default to localhost:2003
      #host            localhost
      #port            2003

      # Optionally specify a source identifier for the metric data sent to
      # Graphite. This can help differentiate data from multiple sources for the
      # same hosts.
      #
      # Result is:
      # host.GRAPHITE_DATA_SOURCE.service.metric
      # instead of:
      # host.service.metric
      #
      # Note: You must set the same value in this module and in the
      # Graphite UI module configuration.
      # default: the variable is unset
      #graphite_data_source shinken

      # Optionnaly specify a latency management
      # If this parameter is enabled the metric time will be change to remove latency
      # For example if the check was scheduled at 0 but was done at 2,
      # the timestamp associated to the data will be 0
      # Basically this ignore small latency in order to have regular interval between data.
      # We skip an Graphite limitation that expect a specific timestamp range for data.
      #ignore_latency_limit 15

      # Optionnaly specify a service description for host check metrics
      #
      # Graphite stores host check metrics in the host directory whereas services
      # are stored in host.service directory. Host check metrics may be stored in their own
      # directory if it is specified.
      #
      # default: __HOST__
      #hostcheck           __HOST__

      # Optionnaly specify filtered metrics
      # Filtered metrics will not be sent to Carbon/Graphite
      #
      # Declare a filter parameter for each service to be filtered:
      # filter    service_description:metrics
      #
      # metrics is a comma separated list of the metrics to be filtered
      # default: no filtered metrics
      #filter           cpu:1m,5m
      #filter           mem:3z

      # Optionnaly specify extra metrics
      # warning, critical, min and max information for the metrics are not often necessary
      # in Graphite
      # You may specify which one are to be sent or not
      # Default is not to send anything else than the metric value
      #send_warning      False
      #send_critical     False
      #send_min          False
      #send_max          False
   }
```
