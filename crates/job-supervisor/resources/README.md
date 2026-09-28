# Resource budget facade

Resource policy is implemented by the scheduler crate; this facade gives the
supervisor a stable namespace for resource accounting without adding another
lock or durable-state writer.
