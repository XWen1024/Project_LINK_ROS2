#ifndef VOLC_S2S_SMOKE_WAV_WRITER_H
#define VOLC_S2S_SMOKE_WAV_WRITER_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

typedef struct {
    FILE *file;
    uint32_t sample_rate;
    uint16_t channels;
    uint16_t bits_per_sample;
    uint32_t data_bytes;
} wav_writer_t;

int wav_writer_open(
    wav_writer_t *writer,
    const char *path,
    uint32_t sample_rate,
    uint16_t channels,
    uint16_t bits_per_sample);
int wav_writer_write(wav_writer_t *writer, const void *data, size_t length);
int wav_writer_close(wav_writer_t *writer);

#endif

