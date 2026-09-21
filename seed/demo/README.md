# Synthetic demonstration data

Every row here is invented. It exists so the whole workflow can be run end to
end without any gated API, and for no other purpose.

It is isolated three ways, not one:

1. The source rows are inserted with `dataset = 'synthetic_demo'`.
2. Every sale and listing is inserted with the same label.
3. A database trigger refuses to attach a synthetic row to a production source
   or the reverse, so demo data cannot be laundered into a real marketplace by
   a careless import.

Production queries filter on `dataset = 'production'`. If you ever see these
numbers in a recommendation, that is a bug worth stopping for.
