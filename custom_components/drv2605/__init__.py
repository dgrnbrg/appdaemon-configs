from esphome import pins
import hashlib
import esphome.codegen as cg
from esphome import automation
import esphome.config_validation as cv
from esphome.components import i2c, sensor
from esphome.const import CONF_ID
import math

DEPENDENCIES = ['i2c']

CONF_I2C_ADDR = 0x5A
CONF_LRA_WAVEFORM = "waveform"
CONF_EN_PIN = "en_pin"
CONF_RATED_VOLTAGE = "rated_voltage"
CONF_RESONANT_FREQUENCY = "resonant_frequency"

drv2605_ns = cg.esphome_ns.namespace('drv2605')
DRV2605Component = drv2605_ns.class_('DRV2605Component', cg.Component, i2c.I2CDevice)
FireHapticAction = drv2605_ns.class_("FireHapticAction", automation.Action)
CalibrateAction = drv2605_ns.class_("CalibrateAction", automation.Action)
ResetAction = drv2605_ns.class_("ResetAction", automation.Action)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(DRV2605Component),
    cv.Required(CONF_EN_PIN): pins.internal_gpio_output_pin_schema,
    cv.Required(CONF_RATED_VOLTAGE): cv.voltage,
    cv.Required(CONF_RESONANT_FREQUENCY): cv.frequency,
}).extend(cv.COMPONENT_SCHEMA).extend(i2c.i2c_device_schema(CONF_I2C_ADDR))

@automation.register_action("drv2605.fire_haptic", FireHapticAction,
    cv.Schema(
        {
            cv.Required(CONF_ID): cv.use_id(DRV2605Component),
            cv.Required(CONF_LRA_WAVEFORM): cv.templatable(cv.int_range(1,127)),
        }
    )
)
async def drv2605_fire_haptic_to_code(config, action_id, template_arg, args):
    paren = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, paren)
    template_ = await cg.templatable(config[CONF_LRA_WAVEFORM], args, int)
    cg.add(var.set_waveform_id(template_))
    return var

@automation.register_action("drv2605.calibrate", CalibrateAction,
    cv.Schema(
        {
            cv.Required(CONF_ID): cv.use_id(DRV2605Component),
        }
    )
)
async def drv2605_calibrate_to_code(config, action_id, template_arg, args):
    paren = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, paren)
    return var

@automation.register_action("drv2605.reset", ResetAction,
    cv.Schema(
        {
            cv.Required(CONF_ID): cv.use_id(DRV2605Component),
        }
    )
)
async def drv2605_reset_to_code(config, action_id, template_arg, args):
    paren = await cg.get_variable(config[CONF_ID])
    var = cg.new_Pvariable(action_id, template_arg, paren)
    return var


async def to_code(config):
    lra_rated_voltage = config[CONF_RATED_VOLTAGE]
    lra_freq = config[CONF_RESONANT_FREQUENCY]
    print(f"rated voltage = {lra_rated_voltage}      resonant freq = {lra_freq}")
    rated_voltage_reg = lra_rated_voltage / 20.58e-3 * math.sqrt(1-(4*300e-6+300e-6) * lra_freq)
    # 72.78418503101999
    print(f"Calculated LRA rated voltage register to be set to {rated_voltage_reg}")
    #overdrive_reg = lra_rated_voltage / (21.32e-3 * math.sqrt(1 - 800e-6 * 205))
    overdrive_reg = lra_rated_voltage * 255 / 5.6 # from some xlsx helper?
    print(f"Calculated LRA overdrive register to be set to {overdrive_reg}")
    # 92.33836194508126
    lra_period_ms = (1.0 / lra_freq) * 1000
    optimum_drive_time = lra_period_ms * 0.5;
    drive_time_reg = (optimum_drive_time - 0.5) / 0.1
    print(f"Calculated drive time register to be set to {drive_time_reg}")
    cv.int_range(0,255)(int(overdrive_reg))
    cv.int_range(0,255)(int(rated_voltage_reg))
    cv.int_range(0,31)(int(drive_time_reg))
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    en_pin_var = await cg.gpio_pin_expression(config[CONF_EN_PIN])

    # hash the name to save prefs
    hash_ = int(hashlib.md5(config[CONF_ID].id.encode()).hexdigest()[:8], 16)
    print(f"DRV2605 name hash is {hex(hash_)}")
    cg.add(var.set_name_hash(hash_))

    cg.add(var.set_en_pin(en_pin_var))
    cg.add(var.set_rated_voltage_reg(int(rated_voltage_reg)))
    cg.add(var.set_overdrive_reg(int(overdrive_reg)))
    cg.add(var.set_drive_time_reg_value(int(drive_time_reg)))

