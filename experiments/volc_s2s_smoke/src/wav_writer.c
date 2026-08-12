#include "wav_writer.h"

#include <string.h>

static int write_u16_le(FILE *file, uint16_t value) {
    unsigned char bytes[2] = {
        (unsigned char)(value & 0xffu),
        (unsigned char)((value >> 8u) & 0xffu),
    };
    return fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes) ? 0 : -1;
}

static int write_u32_le(FILE *file, uint32_t value) {
    unsigned char bytes[4] = {
        (unsigned char)(value & 0xffu),
        (unsigned char)((value >> 8u) & 0xffu),
        (unsigned char)((value >> 16u) & 0xffu),
        (unsigned char)((value >> 24u) & 0xffu),
    };
    return fwrite(bytes, 1, sizeof(bytes), file) == sizeof(bytes) ? 0 : -1;
}

static int write_header(wav_writer_t *writer) {
    const uint32_t byte_rate =
        writer->sample_rate * writer->channels * writer->bits_per_sample / 8u;
    const uint16_t block_align =
        (uint16_t)(writer->channels * writer->bits_per_sample / 8u);

    if (fwrite("RIFF", 1, 4, writer->file) != 4 ||
        write_u32_le(writer->file, 36u + writer->data_bytes) != 0 ||
        fwrite("WAVEfmt ", 1, 8, writer->file) != 8 ||
        write_u32_le(writer->file, 16u) != 0 ||
        write_u16_le(writer->file, 1u) != 0 ||
        write_u16_le(writer->file, writer->channels) != 0 ||
        write_u32_le(writer->file, writer->sample_rate) != 0 ||
        write_u32_le(writer->file, byte_rate) != 0 ||
        write_u16_le(writer->file, block_align) != 0 ||
        write_u16_le(writer->file, writer->bits_per_sample) != 0 ||
        fwrite("data", 1, 4, writer->file) != 4 ||
        write_u32_le(writer->file, writer->data_bytes) != 0) {
        return -1;
    }
    return 0;
}

int wav_writer_open(
    wav_writer_t *writer,
    const char *path,
    uint32_t sample_rate,
    uint16_t channels,
    uint16_t bits_per_sample) {
    if (writer == NULL || path == NULL || channels == 0 || bits_per_sample == 0) {
        return -1;
    }

    memset(writer, 0, sizeof(*writer));
    writer->sample_rate = sample_rate;
    writer->channels = channels;
    writer->bits_per_sample = bits_per_sample;
    writer->file = fopen(path, "wb+");
    if (writer->file == NULL) {
        return -1;
    }
    if (write_header(writer) != 0) {
        fclose(writer->file);
        memset(writer, 0, sizeof(*writer));
        return -1;
    }
    return 0;
}

int wav_writer_write(wav_writer_t *writer, const void *data, size_t length) {
    if (writer == NULL || writer->file == NULL || data == NULL || length == 0) {
        return -1;
    }
    if (length > UINT32_MAX - writer->data_bytes) {
        return -1;
    }
    if (fwrite(data, 1, length, writer->file) != length) {
        return -1;
    }
    writer->data_bytes += (uint32_t)length;
    return 0;
}

int wav_writer_close(wav_writer_t *writer) {
    int result = 0;
    if (writer == NULL || writer->file == NULL) {
        return 0;
    }
    if (fseek(writer->file, 0, SEEK_SET) != 0 || write_header(writer) != 0) {
        result = -1;
    }
    if (fclose(writer->file) != 0) {
        result = -1;
    }
    writer->file = NULL;
    return result;
}

