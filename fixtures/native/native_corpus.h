#ifndef P4AI_NATIVE_CORPUS_H
#define P4AI_NATIVE_CORPUS_H

#include <stddef.h>
#include <stdint.h>

uint64_t pressure_poly(const uint64_t *data, size_t n);
uint64_t pressure_hash(const uint64_t *data, size_t n);
uint64_t pressure_dot(const uint64_t *left, const uint64_t *right, size_t n);

#endif
