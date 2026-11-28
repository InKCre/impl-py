Stopping an extension saves the requested disabled state before running shutdown hooks, so a cleanup failure does not enable it on restart.
