#include <inttypes.h>
#include <stdio.h>

#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "vl53l0x.h"

#define VL53L0X_ADDRESS 0x29

static const char *TAG = "vl53l0x_bridge";

static esp_err_t create_sensor(i2c_master_bus_handle_t *bus,
                               i2c_master_dev_handle_t *device)
{
    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_VL53L0X_I2C_SDA_GPIO,
        .scl_io_num = CONFIG_VL53L0X_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_config, bus), TAG,
                        "cannot create I2C bus");
    ESP_RETURN_ON_ERROR(i2c_master_probe(*bus, VL53L0X_ADDRESS, 200), TAG,
                        "VL53L0X not found at I2C address 0x29");

    i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = VL53L0X_ADDRESS,
        .scl_speed_hz = CONFIG_VL53L0X_I2C_FREQUENCY_HZ,
    };
    return i2c_master_bus_add_device(*bus, &device_config, device);
}

void app_main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    ESP_LOGI(TAG, "ESP32-C3 VL53L0X USB bridge starting");
    ESP_LOGI(TAG, "SDA=GPIO%d SCL=GPIO%d I2C=%d Hz interval=%d ms",
             CONFIG_VL53L0X_I2C_SDA_GPIO,
             CONFIG_VL53L0X_I2C_SCL_GPIO,
             CONFIG_VL53L0X_I2C_FREQUENCY_HZ,
             CONFIG_VL53L0X_SAMPLE_INTERVAL_MS);

    i2c_master_bus_handle_t bus = NULL;
    i2c_master_dev_handle_t device = NULL;
    esp_err_t err = create_sensor(&bus, &device);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C setup failed: %s", esp_err_to_name(err));
        printf("ERROR,0,I2C_SETUP,%s\r\n", esp_err_to_name(err));
        return;
    }

    vl53l0x_t sensor;
    err = vl53l0x_init(&sensor, device, CONFIG_VL53L0X_IO_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "VL53L0X initialization failed: %s", esp_err_to_name(err));
        printf("ERROR,0,SENSOR_INIT,%s\r\n", esp_err_to_name(err));
        return;
    }

    printf("# VL53L0X_USB_BRIDGE,1\r\n");
    printf("# DATA,sequence,time_ms,distance_mm,range_status\r\n");

    uint32_t sequence = 0;
    TickType_t last_wake = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(CONFIG_VL53L0X_SAMPLE_INTERVAL_MS);

    while (true) {
        vl53l0x_measurement_t measurement;
        err = vl53l0x_read_single(&sensor, &measurement);
        const int64_t time_ms = esp_timer_get_time() / 1000;

        if (err == ESP_OK) {
            printf("DATA,%" PRIu32 ",%" PRId64 ",%u,%u\r\n",
                   sequence++, time_ms, measurement.distance_mm,
                   measurement.range_status);
        } else {
            printf("ERROR,%" PRId64 ",RANGE_READ,%s\r\n",
                   time_ms, esp_err_to_name(err));
            ESP_LOGW(TAG, "range read failed: %s", esp_err_to_name(err));
        }

        vTaskDelayUntil(&last_wake, period);
    }
}
