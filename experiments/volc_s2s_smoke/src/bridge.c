#define _GNU_SOURCE

#include "volc_conv_ai.h"

#include <arpa/inet.h>
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"

#define BRIDGE_PROTOCOL_VERSION 1u
#define BRIDGE_HEADER_BYTES 16u
#define BRIDGE_MAX_COMMAND_BYTES (2u * 1024u * 1024u)
#define BRIDGE_MAX_QUEUE_BYTES (16u * 1024u * 1024u)

enum bridge_message_type {
    BRIDGE_CMD_AUDIO = 1,
    BRIDGE_CMD_COMMIT = 2,
    BRIDGE_CMD_CLEAR = 3,
    BRIDGE_CMD_INTERRUPT = 4,
    BRIDGE_CMD_RAW_JSON = 5,
    BRIDGE_CMD_PING = 6,
    BRIDGE_CMD_SHUTDOWN = 7,

    BRIDGE_EVT_CONTROL = 129,
    BRIDGE_EVT_MESSAGE = 130,
    BRIDGE_EVT_AUDIO = 131,
};

typedef struct bridge_event {
    uint8_t type;
    uint16_t flags;
    uint32_t length;
    uint64_t monotonic_ns;
    uint8_t *payload;
    struct bridge_event *next;
} bridge_event_t;

typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    bridge_event_t *head;
    bridge_event_t *tail;
    size_t queued_bytes;
    uint64_t dropped_events;
    bool stop;
    bool connected;
    bool engine_started;
    int ipc_fd;
    uint64_t connect_start_ns;
    volc_engine_t engine;
} bridge_context_t;

static volatile sig_atomic_t g_signal_stop = 0;

static uint64_t monotonic_ns(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return 0;
    }
    return (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
}

static uint64_t host_to_be64(uint64_t value) {
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return ((uint64_t)htonl((uint32_t)(value & 0xffffffffu)) << 32u) |
           (uint64_t)htonl((uint32_t)(value >> 32u));
#else
    return value;
#endif
}

static uint64_t be64_to_host(uint64_t value) {
    return host_to_be64(value);
}

static int write_full(int fd, const void *data, size_t length) {
    const uint8_t *cursor = (const uint8_t *)data;
    size_t offset = 0;
    while (offset < length) {
        const ssize_t written = send(fd, cursor + offset, length - offset, MSG_NOSIGNAL);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            return -1;
        }
        offset += (size_t)written;
    }
    return 0;
}

static int read_full(int fd, void *data, size_t length) {
    uint8_t *cursor = (uint8_t *)data;
    size_t offset = 0;
    while (offset < length) {
        const ssize_t received = recv(fd, cursor + offset, length - offset, 0);
        if (received < 0 && errno == EINTR) {
            continue;
        }
        if (received <= 0) {
            return -1;
        }
        offset += (size_t)received;
    }
    return 0;
}

static int write_frame(
    int fd,
    uint8_t type,
    uint16_t flags,
    uint64_t timestamp_ns,
    const void *payload,
    uint32_t length) {
    uint8_t header[BRIDGE_HEADER_BYTES] = {0};
    const uint16_t flags_be = htons(flags);
    const uint32_t length_be = htonl(length);
    const uint64_t timestamp_be = host_to_be64(timestamp_ns);
    header[0] = type;
    header[1] = BRIDGE_PROTOCOL_VERSION;
    memcpy(header + 2, &flags_be, sizeof(flags_be));
    memcpy(header + 4, &length_be, sizeof(length_be));
    memcpy(header + 8, &timestamp_be, sizeof(timestamp_be));
    if (write_full(fd, header, sizeof(header)) != 0) {
        return -1;
    }
    if (length > 0 && write_full(fd, payload, length) != 0) {
        return -1;
    }
    return 0;
}

static int read_frame(
    int fd,
    uint8_t *type,
    uint16_t *flags,
    uint64_t *timestamp_ns,
    uint8_t **payload,
    uint32_t *length) {
    uint8_t header[BRIDGE_HEADER_BYTES];
    uint16_t flags_be = 0;
    uint32_t length_be = 0;
    uint64_t timestamp_be = 0;
    if (read_full(fd, header, sizeof(header)) != 0) {
        return -1;
    }
    if (header[1] != BRIDGE_PROTOCOL_VERSION) {
        fprintf(stderr, "[VOLC_BRIDGE] unsupported protocol version=%u\n", header[1]);
        return -1;
    }
    memcpy(&flags_be, header + 2, sizeof(flags_be));
    memcpy(&length_be, header + 4, sizeof(length_be));
    memcpy(&timestamp_be, header + 8, sizeof(timestamp_be));
    *type = header[0];
    *flags = ntohs(flags_be);
    *length = ntohl(length_be);
    *timestamp_ns = be64_to_host(timestamp_be);
    if (*length > BRIDGE_MAX_COMMAND_BYTES) {
        fprintf(stderr, "[VOLC_BRIDGE] command too large=%" PRIu32 "\n", *length);
        return -1;
    }
    *payload = NULL;
    if (*length > 0) {
        *payload = (uint8_t *)malloc((size_t)*length + 1u);
        if (*payload == NULL) {
            return -1;
        }
        if (read_full(fd, *payload, *length) != 0) {
            free(*payload);
            *payload = NULL;
            return -1;
        }
        (*payload)[*length] = 0;
    }
    return 0;
}

static void free_event(bridge_event_t *event) {
    if (event == NULL) {
        return;
    }
    free(event->payload);
    free(event);
}

static int enqueue_event_at(
    bridge_context_t *context,
    uint8_t type,
    uint16_t flags,
    uint64_t timestamp_ns,
    const void *payload,
    size_t length) {
    if (length > UINT32_MAX) {
        return -1;
    }
    bridge_event_t *event = (bridge_event_t *)calloc(1, sizeof(*event));
    if (event == NULL) {
        return -1;
    }
    if (length > 0) {
        event->payload = (uint8_t *)malloc(length);
        if (event->payload == NULL) {
            free(event);
            return -1;
        }
        memcpy(event->payload, payload, length);
    }
    event->type = type;
    event->flags = flags;
    event->length = (uint32_t)length;
    event->monotonic_ns = timestamp_ns;

    pthread_mutex_lock(&context->mutex);
    if (context->stop || context->queued_bytes + length > BRIDGE_MAX_QUEUE_BYTES) {
        context->dropped_events++;
        pthread_mutex_unlock(&context->mutex);
        free_event(event);
        return -1;
    }
    if (context->tail != NULL) {
        context->tail->next = event;
    } else {
        context->head = event;
    }
    context->tail = event;
    context->queued_bytes += length;
    pthread_cond_signal(&context->cond);
    pthread_mutex_unlock(&context->mutex);
    return 0;
}

static int enqueue_event(
    bridge_context_t *context,
    uint8_t type,
    const void *payload,
    size_t length) {
    return enqueue_event_at(context, type, 0, monotonic_ns(), payload, length);
}

static int enqueue_control(bridge_context_t *context, const char *json) {
    return enqueue_event(context, BRIDGE_EVT_CONTROL, json, strlen(json));
}

static void *sender_thread_main(void *argument) {
    bridge_context_t *context = (bridge_context_t *)argument;
    while (true) {
        pthread_mutex_lock(&context->mutex);
        while (!context->stop && context->head == NULL) {
            pthread_cond_wait(&context->cond, &context->mutex);
        }
        if (context->stop && context->head == NULL) {
            pthread_mutex_unlock(&context->mutex);
            break;
        }
        bridge_event_t *event = context->head;
        context->head = event->next;
        if (context->head == NULL) {
            context->tail = NULL;
        }
        context->queued_bytes -= event->length;
        pthread_mutex_unlock(&context->mutex);

        if (write_frame(
                context->ipc_fd,
                event->type,
                event->flags,
                event->monotonic_ns,
                event->payload,
                event->length) != 0) {
            free_event(event);
            pthread_mutex_lock(&context->mutex);
            context->stop = true;
            pthread_cond_broadcast(&context->cond);
            pthread_mutex_unlock(&context->mutex);
            shutdown(context->ipc_fd, SHUT_RDWR);
            break;
        }
        free_event(event);
    }
    return NULL;
}

static void on_signal(int signal_number) {
    (void)signal_number;
    g_signal_stop = 1;
}

static const char *conversation_status_name(volc_conv_status_e status) {
    switch (status) {
        case VOLC_CONV_STATUS_LISTENING:
            return "LISTENING";
        case VOLC_CONV_STATUS_THINKING:
            return "THINKING";
        case VOLC_CONV_STATUS_ANSWERING:
            return "ANSWERING";
        case VOLC_CONV_STATUS_INTERRUPTED:
            return "INTERRUPTED";
        case VOLC_CONV_STATUS_ANSWER_FINISH:
            return "ANSWER_FINISH";
        default:
            return "UNKNOWN";
    }
}

static void on_volc_event(volc_engine_t handle, volc_event_t *event, void *user_data) {
    (void)handle;
    bridge_context_t *context = (bridge_context_t *)user_data;
    char message[256];
    const uint64_t now_ns = monotonic_ns();
    uint64_t connect_ms = 0;
    pthread_mutex_lock(&context->mutex);
    if (event->code == VOLC_EV_CONNECTED) {
        context->connected = true;
        if (context->connect_start_ns > 0 && now_ns >= context->connect_start_ns) {
            connect_ms = (now_ns - context->connect_start_ns) / 1000000ull;
        }
    } else if (event->code == VOLC_EV_DISCONNECTED) {
        context->connected = false;
    }
    pthread_mutex_unlock(&context->mutex);
    snprintf(
        message,
        sizeof(message),
        "{\"event\":\"sdk_event\",\"code\":%d,\"connected\":%s,\"connect_ms\":%" PRIu64 "}",
        event->code,
        event->code == VOLC_EV_CONNECTED ? "true" : "false",
        connect_ms);
    enqueue_event_at(context, BRIDGE_EVT_CONTROL, 0, now_ns, message, strlen(message));
}

static void on_conversation_status(
    volc_engine_t handle,
    volc_conv_status_e status,
    void *user_data) {
    (void)handle;
    bridge_context_t *context = (bridge_context_t *)user_data;
    char message[192];
    snprintf(
        message,
        sizeof(message),
        "{\"event\":\"conversation_status\",\"status\":%d,\"name\":\"%s\"}",
        status,
        conversation_status_name(status));
    enqueue_control(context, message);
}

static void on_audio_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_audio_frame_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    (void)info_ptr;
    bridge_context_t *context = (bridge_context_t *)user_data;
    enqueue_event(context, BRIDGE_EVT_AUDIO, data_ptr, data_len);
}

static void on_video_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_video_frame_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    (void)data_ptr;
    (void)data_len;
    (void)info_ptr;
    bridge_context_t *context = (bridge_context_t *)user_data;
    enqueue_control(context, "{\"event\":\"unexpected_video\"}");
}

static void on_message_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_message_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    (void)info_ptr;
    bridge_context_t *context = (bridge_context_t *)user_data;
    enqueue_event(context, BRIDGE_EVT_MESSAGE, data_ptr, data_len);
}

static char *build_config_json(void) {
    const char *instance_id = getenv("VOLC_INSTANCE_ID");
    const char *product_key = getenv("VOLC_PRODUCT_KEY");
    const char *product_secret = getenv("VOLC_PRODUCT_SECRET");
    const char *device_name = getenv("VOLC_DEVICE_NAME");
    cJSON *root = cJSON_CreateObject();
    cJSON *iot = cJSON_CreateObject();
    cJSON *ws = cJSON_CreateObject();
    cJSON *audio = cJSON_CreateObject();
    if (root == NULL || iot == NULL || ws == NULL || audio == NULL) {
        cJSON_Delete(root);
        cJSON_Delete(iot);
        cJSON_Delete(ws);
        cJSON_Delete(audio);
        return NULL;
    }
    cJSON_AddNumberToObject(root, "ver", 1);
    cJSON_AddItemToObject(root, "iot", iot);
    cJSON_AddStringToObject(iot, "instance_id", instance_id);
    cJSON_AddStringToObject(iot, "product_key", product_key);
    cJSON_AddStringToObject(iot, "product_secret", product_secret);
    cJSON_AddStringToObject(iot, "device_name", device_name);
    cJSON_AddItemToObject(root, "ws", ws);
    cJSON_AddItemToObject(ws, "audio", audio);
    cJSON_AddNumberToObject(audio, "codec", VOLC_AUDIO_CODEC_TYPE_PCM);
    char *json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return json;
}

static bool required_environment_present(void) {
    const char *names[] = {
        "VOLC_BOT_ID",
        "VOLC_INSTANCE_ID",
        "VOLC_PRODUCT_KEY",
        "VOLC_PRODUCT_SECRET",
        "VOLC_DEVICE_NAME",
    };
    bool present = true;
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); ++index) {
        const char *value = getenv(names[index]);
        if (value == NULL || value[0] == '\0') {
            fprintf(stderr, "[VOLC_BRIDGE] missing environment variable %s\n", names[index]);
            present = false;
        }
    }
    return present;
}

static int send_json(volc_engine_t engine, const char *json) {
    volc_message_info_t info = {.is_binary = false};
    const int result = volc_send_message(engine, json, strlen(json), &info);
    return result >= 0 ? 0 : result;
}

static void enqueue_command_result(
    bridge_context_t *context,
    const char *command,
    int result,
    uint64_t client_timestamp_ns) {
    char message[256];
    snprintf(
        message,
        sizeof(message),
        "{\"event\":\"command_result\",\"command\":\"%s\",\"result\":%d,\"client_timestamp_ns\":%" PRIu64 "}",
        command,
        result,
        client_timestamp_ns);
    enqueue_control(context, message);
}

static int process_command(
    bridge_context_t *context,
    uint8_t type,
    uint64_t client_timestamp_ns,
    const uint8_t *payload,
    uint32_t length) {
    int result = 0;
    switch (type) {
        case BRIDGE_CMD_AUDIO: {
            if (payload == NULL || length == 0) {
                return -1;
            }
            volc_audio_frame_info_t info = {
                .data_type = VOLC_AUDIO_DATA_TYPE_PCM,
                .commit = false,
            };
            result = volc_send_audio_data(context->engine, payload, length, &info);
            if (result < 0) {
                enqueue_command_result(context, "audio", result, client_timestamp_ns);
            }
            return result < 0 ? result : 0;
        }
        case BRIDGE_CMD_COMMIT:
            result = send_json(context->engine, "{\"type\":\"input_audio_buffer.commit\"}");
            if (result == 0) {
                result = send_json(
                    context->engine,
                    "{\"type\":\"response.create\",\"response\":{\"modalities\":[\"text\",\"audio\"]}}");
            }
            enqueue_command_result(context, "commit", result, client_timestamp_ns);
            return result;
        case BRIDGE_CMD_CLEAR:
            result = send_json(context->engine, "{\"type\":\"input_audio_buffer.clear\"}");
            enqueue_command_result(context, "clear", result, client_timestamp_ns);
            return result;
        case BRIDGE_CMD_INTERRUPT:
            result = volc_interrupt(context->engine);
            enqueue_command_result(context, "interrupt", result, client_timestamp_ns);
            return result;
        case BRIDGE_CMD_RAW_JSON:
            if (payload == NULL || length == 0) {
                return -1;
            }
            result = send_json(context->engine, (const char *)payload);
            enqueue_command_result(context, "raw_json", result, client_timestamp_ns);
            return result;
        case BRIDGE_CMD_PING:
            enqueue_control(context, "{\"event\":\"pong\"}");
            return 0;
        case BRIDGE_CMD_SHUTDOWN:
            return 1;
        default:
            enqueue_control(context, "{\"event\":\"protocol_error\",\"reason\":\"unknown_command\"}");
            return -1;
    }
}

static int parse_ipc_fd(int argc, char **argv) {
    for (int index = 1; index + 1 < argc; ++index) {
        if (strcmp(argv[index], "--ipc-fd") == 0) {
            char *end = NULL;
            const long value = strtol(argv[index + 1], &end, 10);
            if (end != argv[index + 1] && *end == '\0' && value >= 0 && value <= INT32_MAX) {
                return (int)value;
            }
        }
    }
    return -1;
}

int main(int argc, char **argv) {
    const int ipc_fd = parse_ipc_fd(argc, argv);
    if (ipc_fd < 0) {
        fprintf(stderr, "Usage: %s --ipc-fd FD\n", argv[0]);
        return 2;
    }
    if (!required_environment_present()) {
        return 3;
    }

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);
    signal(SIGPIPE, SIG_IGN);

    bridge_context_t context;
    memset(&context, 0, sizeof(context));
    context.ipc_fd = ipc_fd;
    pthread_mutex_init(&context.mutex, NULL);
    pthread_cond_init(&context.cond, NULL);

    pthread_t sender_thread;
    if (pthread_create(&sender_thread, NULL, sender_thread_main, &context) != 0) {
        fprintf(stderr, "[VOLC_BRIDGE] failed to create sender thread\n");
        return 4;
    }

    char *config_json = build_config_json();
    if (config_json == NULL) {
        enqueue_control(&context, "{\"event\":\"fatal\",\"layer\":\"config\"}");
        g_signal_stop = 1;
    }

    volc_event_handler_t handlers = {
        .on_volc_event = on_volc_event,
        .on_volc_conversation_status = on_conversation_status,
        .on_volc_audio_data = on_audio_data,
        .on_volc_video_data = on_video_data,
        .on_volc_message_data = on_message_data,
    };

    int create_result = -1;
    if (!g_signal_stop) {
        const uint64_t auth_start_ns = monotonic_ns();
        create_result = volc_create(&context.engine, config_json, &handlers, &context);
        const uint64_t auth_done_ns = monotonic_ns();
        char message[320];
        snprintf(
            message,
            sizeof(message),
            "{\"event\":\"device_registration\",\"result\":%d,\"elapsed_ms\":%" PRIu64 ",\"sdk_version\":\"%s\",\"sdk_commit\":\"%s\"}",
            create_result,
            (uint64_t)((auth_done_ns - auth_start_ns) / 1000000ull),
            volc_get_version(),
            VOLC_SDK_COMMIT);
        enqueue_event_at(&context, BRIDGE_EVT_CONTROL, 0, auth_done_ns, message, strlen(message));
    }
    free(config_json);

    int start_result = -1;
    if (create_result == 0 && !g_signal_stop) {
        volc_opt_t options = {
            .mode = VOLC_MODE_WS,
            .bot_id = (char *)getenv("VOLC_BOT_ID"),
            .params = "{\"audio\":{\"codec\":4}}",
        };
        context.connect_start_ns = monotonic_ns();
        start_result = volc_start(context.engine, &options);
        context.engine_started = start_result == 0;
        char message[192];
        snprintf(
            message,
            sizeof(message),
            "{\"event\":\"bridge_started\",\"result\":%d,\"transport\":\"websocket_low_load\"}",
            start_result);
        enqueue_control(&context, message);
    }

    int exit_code = 0;
    while (!g_signal_stop && create_result == 0 && start_result == 0) {
        uint8_t type = 0;
        uint16_t flags = 0;
        uint64_t client_timestamp_ns = 0;
        uint8_t *payload = NULL;
        uint32_t length = 0;
        if (read_frame(
                context.ipc_fd,
                &type,
                &flags,
                &client_timestamp_ns,
                &payload,
                &length) != 0) {
            free(payload);
            break;
        }
        (void)flags;
        const int command_result = process_command(
            &context,
            type,
            client_timestamp_ns,
            payload,
            length);
        free(payload);
        if (command_result == 1) {
            break;
        }
    }

    if (create_result != 0 || start_result != 0) {
        exit_code = 10;
    }
    if (context.engine_started) {
        volc_stop(context.engine);
    }
    usleep(100000);
    if (context.engine != NULL) {
        volc_destroy(context.engine);
        context.engine = NULL;
    }

    pthread_mutex_lock(&context.mutex);
    context.stop = true;
    pthread_cond_broadcast(&context.cond);
    pthread_mutex_unlock(&context.mutex);
    shutdown(context.ipc_fd, SHUT_RDWR);
    pthread_join(sender_thread, NULL);
    close(context.ipc_fd);

    while (context.head != NULL) {
        bridge_event_t *next = context.head->next;
        free_event(context.head);
        context.head = next;
    }
    fprintf(
        stderr,
        "[VOLC_BRIDGE] exit code=%d dropped_events=%" PRIu64 "\n",
        exit_code,
        context.dropped_events);
    pthread_cond_destroy(&context.cond);
    pthread_mutex_destroy(&context.mutex);
    return exit_code;
}
