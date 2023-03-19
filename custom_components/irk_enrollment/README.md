# Usage

Stick something like:

```yaml
irk_enrollment:
  latest_irk:
    name: Latest IRK
```  

in your esphome config. Pair a phone or tablet that needs to be using resolvable private address passive fingerprinting with the esphome device (it should appear as a bluetooth keyboard or something). Then, the `Latest IRK` sensor will contain the IRK that you can use with the rest of my fingerprinting suite.
