#include "vl53l0x.h"

#include <stdbool.h>
#include <stddef.h>

#include "esp_timer.h"

#define VL53L0X_I2C_TIMEOUT_MS 100

#define REG_SYSRANGE_START                         0x00
#define REG_SYSTEM_SEQUENCE_CONFIG                 0x01
#define REG_SYSTEM_INTERRUPT_CONFIG_GPIO           0x0A
#define REG_SYSTEM_INTERRUPT_CLEAR                 0x0B
#define REG_RESULT_INTERRUPT_STATUS                0x13
#define REG_RESULT_RANGE_STATUS                    0x14
#define REG_MSRC_CONFIG_CONTROL                    0x60
#define REG_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN  0x44
#define REG_GLOBAL_CONFIG_SPAD_ENABLES_REF_0        0xB0
#define REG_GLOBAL_CONFIG_REF_EN_START_SELECT       0xB6
#define REG_DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD     0x4E
#define REG_DYNAMIC_SPAD_REF_EN_START_OFFSET        0x4F
#define REG_GPIO_HV_MUX_ACTIVE_HIGH                 0x84
#define REG_IDENTIFICATION_MODEL_ID                 0xC0

#define RETURN_ON_ERROR(expression) do { \
    esp_err_t _err = (expression);       \
    if (_err != ESP_OK) {                \
        return _err;                     \
    }                                    \
} while (0)

typedef struct {
    uint8_t reg;
    uint8_t value;
} register_value_t;

static const register_value_t default_tuning[] = {
    {0xFF, 0x01}, {0x00, 0x00}, {0xFF, 0x00}, {0x09, 0x00},
    {0x10, 0x00}, {0x11, 0x00}, {0x24, 0x01}, {0x25, 0xFF},
    {0x75, 0x00}, {0xFF, 0x01}, {0x4E, 0x2C}, {0x48, 0x00},
    {0x30, 0x20}, {0xFF, 0x00}, {0x30, 0x09}, {0x54, 0x00},
    {0x31, 0x04}, {0x32, 0x03}, {0x40, 0x83}, {0x46, 0x25},
    {0x60, 0x00}, {0x27, 0x00}, {0x50, 0x06}, {0x51, 0x00},
    {0x52, 0x96}, {0x56, 0x08}, {0x57, 0x30}, {0x61, 0x00},
    {0x62, 0x00}, {0x64, 0x00}, {0x65, 0x00}, {0x66, 0xA0},
    {0xFF, 0x01}, {0x22, 0x32}, {0x47, 0x14}, {0x49, 0xFF},
    {0x4A, 0x00}, {0xFF, 0x00}, {0x7A, 0x0A}, {0x7B, 0x00},
    {0x78, 0x21}, {0xFF, 0x01}, {0x23, 0x34}, {0x42, 0x00},
    {0x44, 0xFF}, {0x45, 0x26}, {0x46, 0x05}, {0x40, 0x40},
    {0x0E, 0x06}, {0x20, 0x1A}, {0x43, 0x40}, {0xFF, 0x00},
    {0x34, 0x03}, {0x35, 0x44}, {0xFF, 0x01}, {0x31, 0x04},
    {0x4B, 0x09}, {0x4C, 0x05}, {0x4D, 0x04}, {0xFF, 0x00},
    {0x44, 0x00}, {0x45, 0x20}, {0x47, 0x08}, {0x48, 0x28},
    {0x67, 0x00}, {0x70, 0x04}, {0x71, 0x01}, {0x72, 0xFE},
    {0x76, 0x00}, {0x77, 0x00}, {0xFF, 0x01}, {0x0D, 0x01},
    {0xFF, 0x00}, {0x80, 0x01}, {0x01, 0xF8}, {0xFF, 0x01},
    {0x8E, 0x01}, {0x00, 0x01}, {0xFF, 0x00}, {0x80, 0x00},
};

static esp_err_t write_u8(vl53l0x_t *sensor, uint8_t reg, uint8_t value)
{
    uint8_t tx[2] = {reg, value};
    return i2c_master_transmit(sensor->i2c_dev, tx, sizeof(tx),
                               VL53L0X_I2C_TIMEOUT_MS);
}

static esp_err_t write_u16(vl53l0x_t *sensor, uint8_t reg, uint16_t value)
{
    uint8_t tx[3] = {reg, (uint8_t)(value >> 8), (uint8_t)value};
    return i2c_master_transmit(sensor->i2c_dev, tx, sizeof(tx),
                               VL53L0X_I2C_TIMEOUT_MS);
}

static esp_err_t write_bytes(vl53l0x_t *sensor, uint8_t reg,
                             const uint8_t *data, size_t length)
{
    if (length > 16) {
        return ESP_ERR_INVALID_SIZE;
    }

    uint8_t tx[17];
    tx[0] = reg;
    for (size_t i = 0; i < length; ++i) {
        tx[i + 1] = data[i];
    }
    return i2c_master_transmit(sensor->i2c_dev, tx, length + 1,
                               VL53L0X_I2C_TIMEOUT_MS);
}

static esp_err_t read_bytes(vl53l0x_t *sensor, uint8_t reg,
                            uint8_t *data, size_t length)
{
    return i2c_master_transmit_receive(sensor->i2c_dev, &reg, 1, data, length,
                                       VL53L0X_I2C_TIMEOUT_MS);
}

static esp_err_t read_u8(vl53l0x_t *sensor, uint8_t reg, uint8_t *value)
{
    return read_bytes(sensor, reg, value, 1);
}

static bool deadline_expired(int64_t start_us, uint32_t timeout_ms)
{
    return (esp_timer_get_time() - start_us) >= ((int64_t)timeout_ms * 1000);
}

static esp_err_t get_spad_info(vl53l0x_t *sensor, uint8_t *count,
                               bool *is_aperture)
{
    uint8_t value = 0;

    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x06));
    RETURN_ON_ERROR(read_u8(sensor, 0x83, &value));
    RETURN_ON_ERROR(write_u8(sensor, 0x83, value | 0x04));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x07));
    RETURN_ON_ERROR(write_u8(sensor, 0x81, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x94, 0x6B));
    RETURN_ON_ERROR(write_u8(sensor, 0x83, 0x00));

    const int64_t start_us = esp_timer_get_time();
    do {
        RETURN_ON_ERROR(read_u8(sensor, 0x83, &value));
        if (value != 0) {
            break;
        }
        if (deadline_expired(start_us, sensor->io_timeout_ms)) {
            return ESP_ERR_TIMEOUT;
        }
    } while (true);

    RETURN_ON_ERROR(write_u8(sensor, 0x83, 0x01));
    RETURN_ON_ERROR(read_u8(sensor, 0x92, &value));
    *count = value & 0x7F;
    *is_aperture = (value & 0x80) != 0;

    RETURN_ON_ERROR(write_u8(sensor, 0x81, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x06));
    RETURN_ON_ERROR(read_u8(sensor, 0x83, &value));
    RETURN_ON_ERROR(write_u8(sensor, 0x83, value & (uint8_t)~0x04));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x00));
    return ESP_OK;
}

static esp_err_t perform_single_ref_calibration(vl53l0x_t *sensor,
                                                 uint8_t vhv_init_byte)
{
    uint8_t value = 0;
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSRANGE_START,
                             (uint8_t)(0x01 | vhv_init_byte)));

    const int64_t start_us = esp_timer_get_time();
    do {
        RETURN_ON_ERROR(read_u8(sensor, REG_RESULT_INTERRUPT_STATUS, &value));
        if ((value & 0x07) != 0) {
            break;
        }
        if (deadline_expired(start_us, sensor->io_timeout_ms)) {
            return ESP_ERR_TIMEOUT;
        }
    } while (true);

    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_INTERRUPT_CLEAR, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSRANGE_START, 0x00));
    return ESP_OK;
}

esp_err_t vl53l0x_init(vl53l0x_t *sensor,
                       i2c_master_dev_handle_t i2c_dev,
                       uint32_t io_timeout_ms)
{
    if (sensor == NULL || i2c_dev == NULL || io_timeout_ms == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    *sensor = (vl53l0x_t) {
        .i2c_dev = i2c_dev,
        .stop_variable = 0,
        .io_timeout_ms = io_timeout_ms,
    };

    uint8_t value = 0;
    RETURN_ON_ERROR(read_u8(sensor, REG_IDENTIFICATION_MODEL_ID, &value));
    if (value != 0xEE) {
        return ESP_ERR_INVALID_RESPONSE;
    }

    /* The common breakout boards power the sensor I/O domain at 2.8 V. */
    RETURN_ON_ERROR(read_u8(sensor, 0x89, &value));
    RETURN_ON_ERROR(write_u8(sensor, 0x89, value | 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x88, 0x00));

    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x00));
    RETURN_ON_ERROR(read_u8(sensor, 0x91, &sensor->stop_variable));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x00));

    RETURN_ON_ERROR(read_u8(sensor, REG_MSRC_CONFIG_CONTROL, &value));
    RETURN_ON_ERROR(write_u8(sensor, REG_MSRC_CONFIG_CONTROL, value | 0x12));
    /* 0.25 MCPS in 9.7 fixed-point format. */
    RETURN_ON_ERROR(write_u16(sensor,
                              REG_FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN,
                              32));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_SEQUENCE_CONFIG, 0xFF));

    uint8_t spad_count = 0;
    bool spad_is_aperture = false;
    RETURN_ON_ERROR(get_spad_info(sensor, &spad_count, &spad_is_aperture));

    uint8_t spad_map[6] = {0};
    RETURN_ON_ERROR(read_bytes(sensor, REG_GLOBAL_CONFIG_SPAD_ENABLES_REF_0,
                               spad_map, sizeof(spad_map)));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, REG_DYNAMIC_SPAD_REF_EN_START_OFFSET, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, REG_DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD, 0x2C));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, REG_GLOBAL_CONFIG_REF_EN_START_SELECT, 0xB4));

    const uint8_t first_spad = spad_is_aperture ? 12 : 0;
    uint8_t enabled_spads = 0;
    for (uint8_t i = 0; i < 48; ++i) {
        if (i < first_spad || enabled_spads == spad_count) {
            spad_map[i / 8] &= (uint8_t)~(1U << (i % 8));
        } else if ((spad_map[i / 8] >> (i % 8)) & 0x01U) {
            ++enabled_spads;
        }
    }
    RETURN_ON_ERROR(write_bytes(sensor, REG_GLOBAL_CONFIG_SPAD_ENABLES_REF_0,
                                spad_map, sizeof(spad_map)));

    for (size_t i = 0; i < sizeof(default_tuning) / sizeof(default_tuning[0]); ++i) {
        RETURN_ON_ERROR(write_u8(sensor, default_tuning[i].reg,
                                 default_tuning[i].value));
    }

    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04));
    RETURN_ON_ERROR(read_u8(sensor, REG_GPIO_HV_MUX_ACTIVE_HIGH, &value));
    RETURN_ON_ERROR(write_u8(sensor, REG_GPIO_HV_MUX_ACTIVE_HIGH,
                             value & (uint8_t)~0x10));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_INTERRUPT_CLEAR, 0x01));

    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_SEQUENCE_CONFIG, 0x01));
    RETURN_ON_ERROR(perform_single_ref_calibration(sensor, 0x40));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_SEQUENCE_CONFIG, 0x02));
    RETURN_ON_ERROR(perform_single_ref_calibration(sensor, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_SEQUENCE_CONFIG, 0xE8));
    return ESP_OK;
}

esp_err_t vl53l0x_read_single(vl53l0x_t *sensor,
                              vl53l0x_measurement_t *measurement)
{
    if (sensor == NULL || measurement == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t value = 0;
    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0x91, sensor->stop_variable));
    RETURN_ON_ERROR(write_u8(sensor, 0x00, 0x01));
    RETURN_ON_ERROR(write_u8(sensor, 0xFF, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, 0x80, 0x00));
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSRANGE_START, 0x01));

    int64_t start_us = esp_timer_get_time();
    do {
        RETURN_ON_ERROR(read_u8(sensor, REG_SYSRANGE_START, &value));
        if ((value & 0x01) == 0) {
            break;
        }
        if (deadline_expired(start_us, sensor->io_timeout_ms)) {
            return ESP_ERR_TIMEOUT;
        }
    } while (true);

    start_us = esp_timer_get_time();
    do {
        RETURN_ON_ERROR(read_u8(sensor, REG_RESULT_INTERRUPT_STATUS, &value));
        if ((value & 0x07) != 0) {
            break;
        }
        if (deadline_expired(start_us, sensor->io_timeout_ms)) {
            return ESP_ERR_TIMEOUT;
        }
    } while (true);

    uint8_t result[12] = {0};
    RETURN_ON_ERROR(read_bytes(sensor, REG_RESULT_RANGE_STATUS,
                               result, sizeof(result)));
    const uint8_t device_status = (result[0] & 0x78) >> 3;
    /* VL53L0X's internal status 11 means "range complete/valid". */
    measurement->range_status = (device_status == 11) ? 0 : device_status;
    measurement->distance_mm = ((uint16_t)result[10] << 8) | result[11];
    RETURN_ON_ERROR(write_u8(sensor, REG_SYSTEM_INTERRUPT_CLEAR, 0x01));
    return ESP_OK;
}
