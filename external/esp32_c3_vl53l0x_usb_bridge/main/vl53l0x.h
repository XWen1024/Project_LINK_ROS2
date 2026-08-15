#pragma once

#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

typedef struct {
    i2c_master_dev_handle_t i2c_dev;
    uint8_t stop_variable;
    uint32_t io_timeout_ms;
} vl53l0x_t;

typedef struct {
    uint16_t distance_mm;
    uint8_t range_status;
} vl53l0x_measurement_t;

esp_err_t vl53l0x_init(vl53l0x_t *sensor,
                       i2c_master_dev_handle_t i2c_dev,
                       uint32_t io_timeout_ms);

esp_err_t vl53l0x_read_single(vl53l0x_t *sensor,
                              vl53l0x_measurement_t *measurement);
