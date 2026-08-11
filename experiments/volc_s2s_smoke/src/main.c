#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"
#include "volc_conv_ai.h"
#include "wav_writer.h"

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

#ifndef VOLC_SDK_COMMIT
#define VOLC_SDK_COMMIT "unknown"
#endif

#ifndef VOLC_MBEDTLS_COMMIT
#define VOLC_MBEDTLS_COMMIT "unknown"
#endif

#define AUDIO_SAMPLE_RATE 16000u
#define AUDIO_CHANNELS 1u
#define AUDIO_BITS_PER_SAMPLE 16u
#define DEFAULT_FRAME_MS 100
#define DEFAULT_CONNECT_TIMEOUT_SEC 20
#define DEFAULT_RESPONSE_TIMEOUT_SEC 45
#define MAX_LOGGED_JSON 16384u

typedef struct {
    const char *pcm_path;
    const char *artifact_dir;
    int frame_ms;
    int connect_timeout_sec;
    int response_timeout_sec;
    bool expect_function_call;
} cli_options_t;

typedef struct {
    pthread_mutex_t mutex;
    volc_engine_t engine;
    FILE *response_pcm;
    FILE *function_log;
    wav_writer_t response_wav;
    bool wav_open;
    bool connected;
    bool disconnected;
    bool response_done;
    bool function_call_received;
    bool function_task_started;
    bool function_task_finished;
    bool function_output_returned;
    bool response_create_sent;
    bool final_response_after_function;
    size_t total_audio_bytes;
    size_t audio_bytes_at_function_output;
    unsigned response_done_count;
    int last_sdk_event;
    int64_t process_start_ms;
    int64_t t_connect_start_ms;
    int64_t t_connected_ms;
    int64_t t_first_input_audio_ms;
    int64_t t_last_input_audio_ms;
    int64_t t_speech_started_ms;
    int64_t t_speech_stopped_ms;
    int64_t t_first_ai_audio_ms;
    int64_t t_response_done_ms;
    int64_t t_function_call_ms;
    int64_t t_function_output_ms;
    int64_t t_first_ai_audio_after_function_ms;
    char pending_call_id[256];
    char pending_function_name[128];
    char pending_arguments[2048];
} smoke_context_t;

typedef struct {
    smoke_context_t *context;
    char call_id[256];
    char function_name[128];
    char arguments[2048];
} function_task_t;

static volatile sig_atomic_t g_stop_requested = 0;

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
        return -1;
    }
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void sleep_ms(int milliseconds) {
    struct timespec request = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (long)(milliseconds % 1000) * 1000000L,
    };
    while (nanosleep(&request, &request) != 0 && errno == EINTR) {
    }
}

static void handle_signal(int signal_number) {
    (void)signal_number;
    g_stop_requested = 1;
}

static const char *conversation_status_name(volc_conv_status_e status) {
    switch (status) {
        case VOLC_CONV_STATUS_LISTENING:
            return "VOLC_CONV_STATUS_LISTENING";
        case VOLC_CONV_STATUS_THINKING:
            return "VOLC_CONV_STATUS_THINKING";
        case VOLC_CONV_STATUS_ANSWERING:
            return "VOLC_CONV_STATUS_ANSWERING";
        case VOLC_CONV_STATUS_INTERRUPTED:
            return "VOLC_CONV_STATUS_INTERRUPTED";
        case VOLC_CONV_STATUS_ANSWER_FINISH:
            return "VOLC_CONV_STATUS_ANSWER_FINISH";
        default:
            return "VOLC_CONV_STATUS_UNKNOWN";
    }
}

static const char *event_name(volc_event_code_e code) {
    switch (code) {
        case VOLC_EV_CONNECTED:
            return "VOLC_EV_CONNECTED";
        case VOLC_EV_DISCONNECTED:
            return "VOLC_EV_DISCONNECTED";
        case VOLC_EV_QUOTA_EXCEEDED:
            return "VOLC_EV_QUOTA_EXCEEDED";
        default:
            return "VOLC_EV_UNKNOWN";
    }
}

static bool is_sensitive_key(const char *key) {
    if (key == NULL) {
        return false;
    }
    return strcasecmp(key, "product_secret") == 0 ||
           strcasecmp(key, "device_secret") == 0 ||
           strcasecmp(key, "signature") == 0 ||
           strcasecmp(key, "token") == 0 ||
           strcasecmp(key, "authorization") == 0 ||
           strcasecmp(key, "password") == 0;
}

static void redact_json(cJSON *node) {
    if (node == NULL) {
        return;
    }
    for (cJSON *child = node->child; child != NULL; child = child->next) {
        if (is_sensitive_key(child->string) && cJSON_IsString(child)) {
            cJSON_SetValuestring(child, "***REDACTED***");
        } else {
            redact_json(child);
        }
    }
}

static char *sanitized_json_string(const cJSON *root) {
    cJSON *copy = cJSON_Duplicate(root, true);
    if (copy == NULL) {
        return NULL;
    }
    redact_json(copy);
    char *text = cJSON_PrintUnformatted(copy);
    cJSON_Delete(copy);
    return text;
}

static void write_function_debug(
    smoke_context_t *context,
    const char *direction,
    const char *event_type,
    const char *call_id,
    const char *function_name,
    const char *arguments,
    const char *result,
    const cJSON *raw_event) {
    cJSON *record = cJSON_CreateObject();
    if (record == NULL) {
        return;
    }
    cJSON_AddNumberToObject(record, "monotonic_ms", (double)monotonic_ms());
    cJSON_AddStringToObject(record, "direction", direction != NULL ? direction : "unknown");
    cJSON_AddStringToObject(record, "event_type", event_type != NULL ? event_type : "unknown");
    if (call_id != NULL) {
        cJSON_AddStringToObject(record, "call_id", call_id);
    }
    if (function_name != NULL) {
        cJSON_AddStringToObject(record, "function_name", function_name);
    }
    if (arguments != NULL) {
        cJSON_AddStringToObject(record, "arguments", arguments);
    }
    if (result != NULL) {
        cJSON_AddStringToObject(record, "result", result);
    }
    if (raw_event != NULL) {
        cJSON *raw_copy = cJSON_Duplicate(raw_event, true);
        if (raw_copy != NULL) {
            redact_json(raw_copy);
            cJSON_AddItemToObject(record, "raw_event", raw_copy);
        }
    }

    char *line = cJSON_PrintUnformatted(record);
    cJSON_Delete(record);
    if (line == NULL) {
        return;
    }

    pthread_mutex_lock(&context->mutex);
    if (context->function_log != NULL) {
        fprintf(context->function_log, "%s\n", line);
        fflush(context->function_log);
    }
    pthread_mutex_unlock(&context->mutex);
    free(line);
}

static const char *json_string(const cJSON *object, const char *name) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(object, name);
    return cJSON_IsString(item) ? cJSON_GetStringValue(item) : NULL;
}

static void copy_text(char *destination, size_t destination_size, const char *source) {
    if (destination == NULL || destination_size == 0) {
        return;
    }
    if (source == NULL) {
        destination[0] = '\0';
        return;
    }
    snprintf(destination, destination_size, "%s", source);
}

static int send_json_message(smoke_context_t *context, cJSON *root) {
    char *json = cJSON_PrintUnformatted(root);
    if (json == NULL) {
        return -1;
    }
    volc_message_info_t info = {.is_binary = false};
    int result = volc_send_message(context->engine, json, strlen(json), &info);
    free(json);
    return result < 0 ? result : 0;
}

static void *function_output_thread(void *argument) {
    function_task_t *task = (function_task_t *)argument;
    smoke_context_t *context = task->context;
    const char *result_json = "{\"number\":42}";

    cJSON *root = cJSON_CreateObject();
    cJSON *item = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "conversation.item.create");
    cJSON_AddItemToObject(root, "item", item);
    cJSON_AddStringToObject(item, "call_id", task->call_id);
    cJSON_AddStringToObject(item, "type", "function_call_output");
    cJSON_AddStringToObject(item, "object", "realtime.item");
    cJSON_AddStringToObject(item, "output", result_json);

    int output_result = send_json_message(context, root);
    write_function_debug(
        context,
        "client_to_server",
        "conversation.item.create",
        task->call_id,
        task->function_name,
        task->arguments,
        result_json,
        root);
    cJSON_Delete(root);

    bool response_create_sent = false;
    if (output_result == 0) {
        cJSON *response_root = cJSON_CreateObject();
        cJSON *response = cJSON_CreateObject();
        cJSON *modalities = cJSON_CreateArray();
        cJSON_AddStringToObject(response_root, "type", "response.create");
        cJSON_AddItemToObject(response_root, "response", response);
        cJSON_AddItemToArray(modalities, cJSON_CreateString("text"));
        cJSON_AddItemToArray(modalities, cJSON_CreateString("audio"));
        cJSON_AddItemToObject(response, "modalities", modalities);
        response_create_sent = send_json_message(context, response_root) == 0;
        cJSON_Delete(response_root);
    }

    pthread_mutex_lock(&context->mutex);
    context->function_output_returned = output_result == 0;
    context->response_create_sent = response_create_sent;
    context->t_function_output_ms = monotonic_ms();
    context->audio_bytes_at_function_output = context->total_audio_bytes;
    context->function_task_finished = true;
    pthread_mutex_unlock(&context->mutex);

    printf(
        "function_output call_id=%s function=%s result=%s send_status=%d response_create=%s\n",
        task->call_id,
        task->function_name,
        result_json,
        output_result,
        response_create_sent ? "sent" : "not_sent");
    fflush(stdout);
    free(task);
    return NULL;
}

static void schedule_magic_function(
    smoke_context_t *context,
    const char *call_id,
    const char *function_name,
    const char *arguments,
    const cJSON *raw_event,
    const char *event_type) {
    if (call_id == NULL || function_name == NULL) {
        return;
    }

    write_function_debug(
        context,
        "server_to_client",
        event_type,
        call_id,
        function_name,
        arguments,
        NULL,
        raw_event);

    pthread_mutex_lock(&context->mutex);
    context->function_call_received = true;
    context->t_function_call_ms = monotonic_ms();
    copy_text(context->pending_call_id, sizeof(context->pending_call_id), call_id);
    copy_text(
        context->pending_function_name,
        sizeof(context->pending_function_name),
        function_name);
    copy_text(context->pending_arguments, sizeof(context->pending_arguments), arguments);

    if (strcmp(function_name, "get_magic_number") != 0 || context->function_task_started) {
        pthread_mutex_unlock(&context->mutex);
        if (strcmp(function_name, "get_magic_number") != 0) {
            printf("function_call unsupported function=%s call_id=%s\n", function_name, call_id);
            fflush(stdout);
        }
        return;
    }
    context->function_task_started = true;
    pthread_mutex_unlock(&context->mutex);

    function_task_t *task = calloc(1, sizeof(*task));
    if (task == NULL) {
        fprintf(stderr, "ERROR: could not allocate function output task\n");
        return;
    }
    task->context = context;
    copy_text(task->call_id, sizeof(task->call_id), call_id);
    copy_text(task->function_name, sizeof(task->function_name), function_name);
    copy_text(task->arguments, sizeof(task->arguments), arguments);

    pthread_t thread;
    if (pthread_create(&thread, NULL, function_output_thread, task) != 0) {
        fprintf(stderr, "ERROR: could not start function output thread\n");
        pthread_mutex_lock(&context->mutex);
        context->function_task_started = false;
        context->function_task_finished = true;
        pthread_mutex_unlock(&context->mutex);
        free(task);
        return;
    }
    pthread_detach(thread);
}

static void handle_function_item(
    smoke_context_t *context,
    const cJSON *root,
    const cJSON *item,
    const char *event_type) {
    if (!cJSON_IsObject(item)) {
        return;
    }
    const char *item_type = json_string(item, "type");
    if (item_type == NULL || strcmp(item_type, "function_call") != 0) {
        return;
    }
    const char *call_id = json_string(item, "call_id");
    const char *name = json_string(item, "name");
    const char *arguments = json_string(item, "arguments");

    pthread_mutex_lock(&context->mutex);
    if (call_id != NULL) {
        copy_text(context->pending_call_id, sizeof(context->pending_call_id), call_id);
    }
    if (name != NULL) {
        copy_text(context->pending_function_name, sizeof(context->pending_function_name), name);
    }
    if (arguments != NULL) {
        copy_text(context->pending_arguments, sizeof(context->pending_arguments), arguments);
    }
    context->function_call_received = true;
    context->t_function_call_ms = monotonic_ms();
    pthread_mutex_unlock(&context->mutex);

    write_function_debug(
        context,
        "server_to_client",
        event_type,
        call_id,
        name,
        arguments,
        NULL,
        root);
    printf(
        "function_call_received event=%s call_id=%s function=%s arguments=%s\n",
        event_type,
        call_id != NULL ? call_id : "N/A",
        name != NULL ? name : "N/A",
        arguments != NULL ? arguments : "{}");
    fflush(stdout);
}

static void handle_function_arguments_done(
    smoke_context_t *context,
    const cJSON *root,
    const char *event_type) {
    const char *call_id = json_string(root, "call_id");
    const char *name = json_string(root, "name");
    const char *arguments = json_string(root, "arguments");
    char cached_call_id[256];
    char cached_name[128];
    char cached_arguments[2048];

    pthread_mutex_lock(&context->mutex);
    copy_text(cached_call_id, sizeof(cached_call_id), context->pending_call_id);
    copy_text(cached_name, sizeof(cached_name), context->pending_function_name);
    copy_text(cached_arguments, sizeof(cached_arguments), context->pending_arguments);
    pthread_mutex_unlock(&context->mutex);

    schedule_magic_function(
        context,
        call_id != NULL ? call_id : cached_call_id,
        name != NULL ? name : cached_name,
        arguments != NULL ? arguments : cached_arguments,
        root,
        event_type);
}

static void handle_tool_calls(smoke_context_t *context, const cJSON *root) {
    cJSON *tool_calls = cJSON_GetObjectItemCaseSensitive(root, "tool_calls");
    if (!cJSON_IsArray(tool_calls)) {
        return;
    }
    cJSON *tool = NULL;
    cJSON_ArrayForEach(tool, tool_calls) {
        const char *call_id = json_string(tool, "id");
        cJSON *function = cJSON_GetObjectItemCaseSensitive(tool, "function");
        const char *name = cJSON_IsObject(function) ? json_string(function, "name") : NULL;
        const char *arguments =
            cJSON_IsObject(function) ? json_string(function, "arguments") : NULL;
        schedule_magic_function(
            context,
            call_id,
            name,
            arguments,
            root,
            "tool_calls");
    }
}

static void on_volc_event(volc_engine_t handle, volc_event_t *event, void *user_data) {
    (void)handle;
    smoke_context_t *context = (smoke_context_t *)user_data;
    const int64_t now = monotonic_ms();
    pthread_mutex_lock(&context->mutex);
    context->last_sdk_event = event->code;
    if (event->code == VOLC_EV_CONNECTED) {
        context->connected = true;
        context->t_connected_ms = now;
    } else if (event->code == VOLC_EV_DISCONNECTED) {
        context->disconnected = true;
    }
    pthread_mutex_unlock(&context->mutex);
    printf("sdk_event code=%d name=%s\n", event->code, event_name(event->code));
    fflush(stdout);
}

static void on_volc_conversation_status(
    volc_engine_t handle,
    volc_conv_status_e status,
    void *user_data) {
    (void)handle;
    smoke_context_t *context = (smoke_context_t *)user_data;
    const int64_t now = monotonic_ms();
    pthread_mutex_lock(&context->mutex);
    switch (status) {
        case VOLC_CONV_STATUS_LISTENING:
            if (context->t_speech_started_ms < 0) {
                context->t_speech_started_ms = now;
            }
            break;
        case VOLC_CONV_STATUS_THINKING:
            if (context->t_speech_stopped_ms < 0) {
                context->t_speech_stopped_ms = now;
            }
            break;
        case VOLC_CONV_STATUS_ANSWER_FINISH:
            context->response_done = true;
            context->response_done_count++;
            context->t_response_done_ms = now;
            if (context->function_output_returned && now >= context->t_function_output_ms) {
                context->final_response_after_function = true;
            }
            break;
        default:
            break;
    }
    pthread_mutex_unlock(&context->mutex);
    printf("conversation_state value=%d name=%s\n", status, conversation_status_name(status));
    fflush(stdout);
}

static void on_volc_audio_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_audio_frame_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    smoke_context_t *context = (smoke_context_t *)user_data;
    const int64_t now = monotonic_ms();
    bool write_error = false;

    pthread_mutex_lock(&context->mutex);
    if (context->t_first_ai_audio_ms < 0) {
        context->t_first_ai_audio_ms = now;
    }
    if (context->function_output_returned &&
        context->t_first_ai_audio_after_function_ms < 0) {
        context->t_first_ai_audio_after_function_ms = now;
    }
    if (context->response_pcm == NULL ||
        fwrite(data_ptr, 1, data_len, context->response_pcm) != data_len) {
        write_error = true;
    } else {
        fflush(context->response_pcm);
    }
    if (!context->wav_open ||
        wav_writer_write(&context->response_wav, data_ptr, data_len) != 0) {
        write_error = true;
    }
    context->total_audio_bytes += data_len;
    const size_t total = context->total_audio_bytes;
    pthread_mutex_unlock(&context->mutex);

    printf(
        "audio_callback bytes=%zu total=%zu data_type=%d format=PCM_S16LE sample_rate=%u channels=%u%s\n",
        data_len,
        total,
        info_ptr != NULL ? (int)info_ptr->data_type : -1,
        AUDIO_SAMPLE_RATE,
        AUDIO_CHANNELS,
        write_error ? " write_error=true" : "");
    fflush(stdout);
}

static void on_volc_video_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_video_frame_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    (void)data_ptr;
    (void)data_len;
    (void)info_ptr;
    (void)user_data;
    printf("video_callback unexpected=true\n");
}

static void on_volc_message_data(
    volc_engine_t handle,
    const void *data_ptr,
    size_t data_len,
    volc_message_info_t *info_ptr,
    void *user_data) {
    (void)handle;
    smoke_context_t *context = (smoke_context_t *)user_data;
    if (data_ptr == NULL || data_len == 0 || data_len > MAX_LOGGED_JSON) {
        printf("message_callback bytes=%zu skipped=%s\n", data_len, data_len > MAX_LOGGED_JSON ? "too_large" : "empty");
        fflush(stdout);
        return;
    }

    char *message = calloc(data_len + 1, 1);
    if (message == NULL) {
        fprintf(stderr, "ERROR: message allocation failed\n");
        return;
    }
    memcpy(message, data_ptr, data_len);

    cJSON *root = cJSON_ParseWithLength(message, data_len);
    if (root == NULL) {
        printf(
            "message_callback bytes=%zu binary=%s json_parse=failed\n",
            data_len,
            info_ptr != NULL && info_ptr->is_binary ? "true" : "false");
        free(message);
        return;
    }

    const char *type = json_string(root, "type");
    char *sanitized = sanitized_json_string(root);
    printf(
        "message_callback type=%s bytes=%zu binary=%s json=%s\n",
        type != NULL ? type : "unknown",
        data_len,
        info_ptr != NULL && info_ptr->is_binary ? "true" : "false",
        sanitized != NULL ? sanitized : "<serialization_failed>");
    fflush(stdout);
    free(sanitized);

    if (type != NULL &&
        (strcmp(type, "conversation.item.created") == 0 ||
         strcmp(type, "response.output_item.done") == 0)) {
        handle_function_item(
            context,
            root,
            cJSON_GetObjectItemCaseSensitive(root, "item"),
            type);
    } else if (type != NULL &&
               strcmp(type, "response.function_call_arguments.done") == 0) {
        handle_function_arguments_done(context, root, type);
    }
    handle_tool_calls(context, root);

    cJSON_Delete(root);
    free(message);
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
            fprintf(stderr, "ERROR: missing environment variable %s\n", names[index]);
            present = false;
        }
    }
    const char *secret = getenv("VOLC_PRODUCT_SECRET");
    if (secret != NULL && strlen(secret) < 16) {
        fprintf(stderr, "ERROR: VOLC_PRODUCT_SECRET is unexpectedly short (value not logged)\n");
        present = false;
    }
    return present;
}

static int ensure_directory(const char *path) {
    if (mkdir(path, 0700) == 0 || errno == EEXIST) {
        return 0;
    }
    return -1;
}

static int initialize_artifacts(smoke_context_t *context, const char *artifact_dir) {
    char pcm_path[PATH_MAX];
    char wav_path[PATH_MAX];
    char function_log_path[PATH_MAX];
    if (ensure_directory(artifact_dir) != 0) {
        fprintf(stderr, "ERROR: cannot create artifact directory %s: %s\n", artifact_dir, strerror(errno));
        return -1;
    }
    if (snprintf(pcm_path, sizeof(pcm_path), "%s/response.pcm", artifact_dir) >= (int)sizeof(pcm_path) ||
        snprintf(wav_path, sizeof(wav_path), "%s/response.wav", artifact_dir) >= (int)sizeof(wav_path) ||
        snprintf(function_log_path, sizeof(function_log_path), "%s/function_calls.jsonl", artifact_dir) >= (int)sizeof(function_log_path)) {
        fprintf(stderr, "ERROR: artifact path is too long\n");
        return -1;
    }

    context->response_pcm = fopen(pcm_path, "wb");
    context->function_log = fopen(function_log_path, "wb");
    if (context->response_pcm == NULL || context->function_log == NULL) {
        fprintf(stderr, "ERROR: cannot open artifact output files\n");
        return -1;
    }
    if (wav_writer_open(
            &context->response_wav,
            wav_path,
            AUDIO_SAMPLE_RATE,
            AUDIO_CHANNELS,
            AUDIO_BITS_PER_SAMPLE) != 0) {
        fprintf(stderr, "ERROR: cannot open response WAV file\n");
        return -1;
    }
    context->wav_open = true;
    return 0;
}

static void close_artifacts(smoke_context_t *context) {
    pthread_mutex_lock(&context->mutex);
    if (context->response_pcm != NULL) {
        fclose(context->response_pcm);
        context->response_pcm = NULL;
    }
    if (context->function_log != NULL) {
        fclose(context->function_log);
        context->function_log = NULL;
    }
    if (context->wav_open) {
        wav_writer_close(&context->response_wav);
        context->wav_open = false;
    }
    pthread_mutex_unlock(&context->mutex);
}

static bool wait_for_connection(smoke_context_t *context, int timeout_sec) {
    const int64_t deadline = monotonic_ms() + (int64_t)timeout_sec * 1000;
    while (!g_stop_requested && monotonic_ms() < deadline) {
        pthread_mutex_lock(&context->mutex);
        const bool connected = context->connected;
        const bool disconnected = context->disconnected;
        pthread_mutex_unlock(&context->mutex);
        if (connected) {
            return true;
        }
        if (disconnected) {
            return false;
        }
        sleep_ms(50);
    }
    return false;
}

static int send_pcm_file(smoke_context_t *context, const char *path, int frame_ms) {
    FILE *input = fopen(path, "rb");
    if (input == NULL) {
        fprintf(stderr, "ERROR: cannot open PCM input %s: %s\n", path, strerror(errno));
        return -1;
    }
    if (fseek(input, 0, SEEK_END) != 0) {
        fclose(input);
        return -1;
    }
    const long file_size_long = ftell(input);
    if (file_size_long <= 0 || (file_size_long % 2) != 0 || fseek(input, 0, SEEK_SET) != 0) {
        fprintf(stderr, "ERROR: PCM input must be non-empty, headerless, and contain complete S16LE samples\n");
        fclose(input);
        return -1;
    }

    const size_t file_size = (size_t)file_size_long;
    const size_t bytes_per_frame =
        (size_t)AUDIO_SAMPLE_RATE * AUDIO_CHANNELS * (AUDIO_BITS_PER_SAMPLE / 8u) *
        (size_t)frame_ms / 1000u;
    if (bytes_per_frame == 0 || (bytes_per_frame % 2u) != 0) {
        fprintf(stderr, "ERROR: invalid frame duration %d ms\n", frame_ms);
        fclose(input);
        return -1;
    }

    unsigned char *buffer = malloc(bytes_per_frame);
    if (buffer == NULL) {
        fclose(input);
        return -1;
    }

    printf(
        "pcm_upload path=%s bytes=%zu format=PCM_S16LE sample_rate=%u channels=%u frame_ms=%d frame_bytes=%zu\n",
        path,
        file_size,
        AUDIO_SAMPLE_RATE,
        AUDIO_CHANNELS,
        frame_ms,
        bytes_per_frame);
    fflush(stdout);

    size_t total_sent = 0;
    int result = 0;
    while (!g_stop_requested && total_sent < file_size) {
        const size_t remaining = file_size - total_sent;
        const size_t wanted = remaining < bytes_per_frame ? remaining : bytes_per_frame;
        const size_t count = fread(buffer, 1, wanted, input);
        if (count != wanted) {
            fprintf(stderr, "ERROR: short read from PCM input\n");
            result = -1;
            break;
        }
        const bool commit = total_sent + count == file_size;
        volc_audio_frame_info_t info = {
            .data_type = VOLC_AUDIO_DATA_TYPE_PCM,
            .commit = commit,
        };
        const int64_t send_start = monotonic_ms();
        const int send_result = volc_send_audio_data(context->engine, buffer, count, &info);
        if (send_result < 0) {
            fprintf(stderr, "ERROR: volc_send_audio_data returned %d after %zu bytes\n", send_result, total_sent);
            result = -1;
            break;
        }

        pthread_mutex_lock(&context->mutex);
        if (context->t_first_input_audio_ms < 0) {
            context->t_first_input_audio_ms = monotonic_ms();
        }
        context->t_last_input_audio_ms = monotonic_ms();
        pthread_mutex_unlock(&context->mutex);

        total_sent += count;
        printf("pcm_frame bytes=%zu total=%zu commit=%s\n", count, total_sent, commit ? "true" : "false");
        fflush(stdout);
        if (!commit) {
            const int64_t elapsed = monotonic_ms() - send_start;
            if (elapsed < frame_ms) {
                sleep_ms(frame_ms - (int)elapsed);
            }
        }
    }

    free(buffer);
    fclose(input);
    if (result == 0) {
        printf("pcm_upload_complete bytes=%zu\n", total_sent);
    }
    return result;
}

static bool wait_for_response(
    smoke_context_t *context,
    int timeout_sec,
    bool expect_function_call) {
    const int64_t deadline = monotonic_ms() + (int64_t)timeout_sec * 1000;
    while (!g_stop_requested && monotonic_ms() < deadline) {
        pthread_mutex_lock(&context->mutex);
        const bool disconnected = context->disconnected;
        const bool has_audio = context->total_audio_bytes > 0;
        const bool response_done = context->response_done;
        const bool function_call_received = context->function_call_received;
        const bool function_output_returned = context->function_output_returned;
        const bool final_response_after_function = context->final_response_after_function;
        const bool audio_after_function =
            context->total_audio_bytes > context->audio_bytes_at_function_output;
        pthread_mutex_unlock(&context->mutex);

        if (expect_function_call) {
            if (function_call_received && function_output_returned &&
                final_response_after_function && audio_after_function) {
                return true;
            }
        } else if (has_audio && response_done) {
            return true;
        }
        if (disconnected) {
            return false;
        }
        sleep_ms(50);
    }
    return false;
}

static void wait_for_function_task(smoke_context_t *context, int timeout_ms) {
    const int64_t deadline = monotonic_ms() + timeout_ms;
    while (monotonic_ms() < deadline) {
        pthread_mutex_lock(&context->mutex);
        const bool done = !context->function_task_started || context->function_task_finished;
        pthread_mutex_unlock(&context->mutex);
        if (done) {
            return;
        }
        sleep_ms(20);
    }
}

static void print_timestamp(const char *name, int64_t value, int64_t origin) {
    if (value < 0) {
        printf("%s=N/A\n", name);
    } else {
        printf("%s=%" PRId64 "ms\n", name, value - origin);
    }
}

static void print_duration(const char *name, int64_t start, int64_t end) {
    if (start < 0 || end < 0 || end < start) {
        printf("%s=N/A\n", name);
    } else {
        printf("%s=%" PRId64 "ms\n", name, end - start);
    }
}

static void print_metrics(smoke_context_t *context) {
    pthread_mutex_lock(&context->mutex);
    print_timestamp("T0_websocket_connect_start", context->t_connect_start_ms, context->process_start_ms);
    print_timestamp("T1_websocket_connected", context->t_connected_ms, context->process_start_ms);
    print_timestamp("T2_first_input_audio_sent", context->t_first_input_audio_ms, context->process_start_ms);
    print_timestamp("T3_last_input_audio_sent", context->t_last_input_audio_ms, context->process_start_ms);
    print_timestamp("T4_speech_started", context->t_speech_started_ms, context->process_start_ms);
    print_timestamp("T5_speech_stopped", context->t_speech_stopped_ms, context->process_start_ms);
    print_timestamp("T6_first_ai_audio", context->t_first_ai_audio_ms, context->process_start_ms);
    print_timestamp("T7_response_done", context->t_response_done_ms, context->process_start_ms);
    print_duration("connect_ms", context->t_connect_start_ms, context->t_connected_ms);
    print_duration(
        "speech_end_to_first_audio_ms",
        context->t_speech_stopped_ms,
        context->t_first_ai_audio_ms);
    print_duration("response_total_ms", context->t_last_input_audio_ms, context->t_response_done_ms);
    printf("total_audio_bytes=%zu\n", context->total_audio_bytes);
    printf("audio_format=PCM_S16LE\n");
    printf("audio_sample_rate=%u\n", AUDIO_SAMPLE_RATE);
    printf("audio_channels=%u\n", AUDIO_CHANNELS);
    printf("response_done_count=%u\n", context->response_done_count);
    printf("function_call_received=%s\n", context->function_call_received ? "true" : "false");
    printf("function_output_returned=%s\n", context->function_output_returned ? "true" : "false");
    printf("response_create_sent=%s\n", context->response_create_sent ? "true" : "false");
    printf("final_response_after_function=%s\n", context->final_response_after_function ? "true" : "false");
    pthread_mutex_unlock(&context->mutex);
    fflush(stdout);
}

static void usage(const char *program) {
    printf(
        "Usage: %s [options]\n"
        "\n"
        "Options:\n"
        "  --pcm PATH                  Send headerless PCM S16LE/16kHz/mono.\n"
        "  --artifact-dir PATH         Output directory (default: artifacts).\n"
        "  --frame-ms N                Realtime send cadence (default: 100).\n"
        "  --connect-timeout-sec N     Connection timeout (default: 20).\n"
        "  --response-timeout-sec N    Response timeout (default: 45).\n"
        "  --expect-function-call      Require get_magic_number round trip.\n"
        "  --help                      Show this help.\n",
        program);
}

static bool parse_positive_int(const char *text, int *output) {
    char *end = NULL;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value <= 0 || value > INT_MAX) {
        return false;
    }
    *output = (int)value;
    return true;
}

static int parse_args(int argc, char **argv, cli_options_t *options) {
    *options = (cli_options_t){
        .pcm_path = NULL,
        .artifact_dir = "artifacts",
        .frame_ms = DEFAULT_FRAME_MS,
        .connect_timeout_sec = DEFAULT_CONNECT_TIMEOUT_SEC,
        .response_timeout_sec = DEFAULT_RESPONSE_TIMEOUT_SEC,
        .expect_function_call = false,
    };

    for (int index = 1; index < argc; ++index) {
        if (strcmp(argv[index], "--help") == 0) {
            usage(argv[0]);
            return 1;
        }
        if (strcmp(argv[index], "--expect-function-call") == 0) {
            options->expect_function_call = true;
            continue;
        }
        if (index + 1 >= argc) {
            fprintf(stderr, "ERROR: option %s requires a value\n", argv[index]);
            return -1;
        }
        const char *value = argv[++index];
        if (strcmp(argv[index - 1], "--pcm") == 0) {
            options->pcm_path = value;
        } else if (strcmp(argv[index - 1], "--artifact-dir") == 0) {
            options->artifact_dir = value;
        } else if (strcmp(argv[index - 1], "--frame-ms") == 0) {
            if (!parse_positive_int(value, &options->frame_ms) || options->frame_ms > 1000) {
                fprintf(stderr, "ERROR: --frame-ms must be in 1..1000\n");
                return -1;
            }
        } else if (strcmp(argv[index - 1], "--connect-timeout-sec") == 0) {
            if (!parse_positive_int(value, &options->connect_timeout_sec)) {
                fprintf(stderr, "ERROR: invalid connect timeout\n");
                return -1;
            }
        } else if (strcmp(argv[index - 1], "--response-timeout-sec") == 0) {
            if (!parse_positive_int(value, &options->response_timeout_sec)) {
                fprintf(stderr, "ERROR: invalid response timeout\n");
                return -1;
            }
        } else {
            fprintf(stderr, "ERROR: unknown option %s\n", argv[index - 1]);
            return -1;
        }
    }
    if (options->expect_function_call && options->pcm_path == NULL) {
        fprintf(stderr, "ERROR: --expect-function-call requires --pcm\n");
        return -1;
    }
    return 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    cli_options_t options;
    const int args_result = parse_args(argc, argv, &options);
    if (args_result > 0) {
        return 0;
    }
    if (args_result < 0 || !required_environment_present()) {
        return 2;
    }

    smoke_context_t context;
    memset(&context, 0, sizeof(context));
    pthread_mutex_init(&context.mutex, NULL);
    context.process_start_ms = monotonic_ms();
    context.t_connect_start_ms = -1;
    context.t_connected_ms = -1;
    context.t_first_input_audio_ms = -1;
    context.t_last_input_audio_ms = -1;
    context.t_speech_started_ms = -1;
    context.t_speech_stopped_ms = -1;
    context.t_first_ai_audio_ms = -1;
    context.t_response_done_ms = -1;
    context.t_function_call_ms = -1;
    context.t_function_output_ms = -1;
    context.t_first_ai_audio_after_function_ms = -1;

    struct utsname platform;
    if (uname(&platform) != 0) {
        memset(&platform, 0, sizeof(platform));
        snprintf(platform.machine, sizeof(platform.machine), "unknown");
        snprintf(platform.sysname, sizeof(platform.sysname), "unknown");
        snprintf(platform.release, sizeof(platform.release), "unknown");
    }
    printf("architecture=%s\n", platform.machine);
    printf("platform=%s %s\n", platform.sysname, platform.release);
    printf("sdk_commit=%s\n", VOLC_SDK_COMMIT);
    printf("sdk_version=%s\n", volc_get_version());
    printf("mbedtls_commit=%s\n", VOLC_MBEDTLS_COMMIT);
    printf("transport=websocket_low_load\n");
    printf("rtc_transport=disabled\n");
    printf("credentials=present_values_redacted\n");

    if (strcmp(platform.machine, "aarch64") != 0 && strcmp(platform.machine, "arm64") != 0) {
        fprintf(stderr, "ERROR: runtime architecture is not ARM64\n");
        pthread_mutex_destroy(&context.mutex);
        return 3;
    }

    if (initialize_artifacts(&context, options.artifact_dir) != 0) {
        close_artifacts(&context);
        pthread_mutex_destroy(&context.mutex);
        return 4;
    }

    char *config_json = build_config_json();
    if (config_json == NULL) {
        fprintf(stderr, "ERROR: failed to create SDK config\n");
        close_artifacts(&context);
        pthread_mutex_destroy(&context.mutex);
        return 5;
    }

    volc_event_handler_t handlers = {
        .on_volc_event = on_volc_event,
        .on_volc_conversation_status = on_volc_conversation_status,
        .on_volc_audio_data = on_volc_audio_data,
        .on_volc_video_data = on_volc_video_data,
        .on_volc_message_data = on_volc_message_data,
    };

    printf("authentication_registration_start\n");
    const int64_t auth_start = monotonic_ms();
    int sdk_result = volc_create(&context.engine, config_json, &handlers, &context);
    const int64_t auth_done = monotonic_ms();
    free(config_json);
    printf(
        "authentication_registration_result code=%d text=%s elapsed_ms=%" PRId64 "\n",
        sdk_result,
        volc_err_2_str(sdk_result),
        auth_done - auth_start);
    if (sdk_result != 0) {
        fprintf(stderr, "ERROR: volc_create failed; probable layer=authentication_or_device_registration\n");
        close_artifacts(&context);
        pthread_mutex_destroy(&context.mutex);
        return 10;
    }

    volc_opt_t start_options = {
        .mode = VOLC_MODE_WS,
        .bot_id = (char *)getenv("VOLC_BOT_ID"),
        .params = "{\"audio\":{\"codec\":4}}",
    };
    context.t_connect_start_ms = monotonic_ms();
    printf("connecting transport=websocket url_host=ai-gateway.vei.volces.com\n");
    sdk_result = volc_start(context.engine, &start_options);
    printf("volc_start_result code=%d\n", sdk_result);
    if (sdk_result != 0) {
        fprintf(stderr, "ERROR: volc_start failed; probable layer=websocket_initialization\n");
        volc_destroy(context.engine);
        close_artifacts(&context);
        pthread_mutex_destroy(&context.mutex);
        return 11;
    }

    if (!wait_for_connection(&context, options.connect_timeout_sec)) {
        fprintf(stderr, "ERROR: WebSocket did not connect before timeout\n");
        print_metrics(&context);
        volc_stop(context.engine);
        volc_destroy(context.engine);
        close_artifacts(&context);
        pthread_mutex_destroy(&context.mutex);
        return 12;
    }
    printf("connected transport=websocket\n");

    int exit_code = 0;
    if (options.pcm_path != NULL) {
        if (send_pcm_file(&context, options.pcm_path, options.frame_ms) != 0) {
            exit_code = 20;
        } else {
            const bool completed = wait_for_response(
                &context,
                options.response_timeout_sec,
                options.expect_function_call);
            if (!completed) {
                fprintf(stderr, "ERROR: expected response condition was not met before timeout\n");
                exit_code = options.expect_function_call ? 24 : 21;
            }
        }
    } else {
        printf("connect_only=true pcm_upload=not_requested\n");
        sleep_ms(1000);
    }

    print_metrics(&context);

    pthread_mutex_lock(&context.mutex);
    const size_t total_audio_bytes = context.total_audio_bytes;
    const bool function_call_received = context.function_call_received;
    const bool function_output_returned = context.function_output_returned;
    const bool final_response_after_function = context.final_response_after_function;
    pthread_mutex_unlock(&context.mutex);

    if (exit_code == 0 && options.pcm_path != NULL && total_audio_bytes == 0) {
        fprintf(stderr, "ERROR: no AI audio was received\n");
        exit_code = 22;
    }
    if (exit_code == 0 && options.expect_function_call &&
        (!function_call_received || !function_output_returned || !final_response_after_function)) {
        fprintf(stderr, "ERROR: Function Calling round trip incomplete\n");
        exit_code = 23;
    }

    wait_for_function_task(&context, 2000);
    const int stop_result = volc_stop(context.engine);
    printf("volc_stop_result code=%d\n", stop_result);
    sleep_ms(100);
    volc_destroy(context.engine);
    context.engine = NULL;
    close_artifacts(&context);
    pthread_mutex_destroy(&context.mutex);

    printf("disconnected clean_shutdown=true exit_code=%d\n", exit_code);
    return exit_code;
}
