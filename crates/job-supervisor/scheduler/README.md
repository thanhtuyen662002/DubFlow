# Fair batch scheduler

The scheduler uses bounded resource-class capacities, priority plus capped
aging, and checkpoint-safe quantum yields. A job holds resources only for one
dispatch slice; completing or yielding that slice releases them for the next
eligible job. Poisoned jobs are removed from active usage without stopping the
queue. Disk and debug/cache retention guards are pure decisions.
