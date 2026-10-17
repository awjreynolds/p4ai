#define _POSIX_C_SOURCE 200809L

#include "native_corpus.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#define VECTOR_CAPACITY 64
#define LANE_COUNT 40

static uint64_t next_random(uint64_t *state) {
    uint64_t value = (*state += UINT64_C(0x9e3779b97f4a7c15));
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
}

static void fill_inputs(uint64_t *left, uint64_t *right, size_t n, uint64_t seed) {
    static const uint64_t edges[] = {
        0, 1, 2, UINT64_MAX, UINT64_MAX - 1, UINT64_C(1) << 63,
        (UINT64_C(1) << 63) - 1, UINT64_C(0xaaaaaaaaaaaaaaaa),
    };
    uint64_t random_state = seed;
    for (size_t i = 0; i < n; ++i) {
        if (i < sizeof(edges) / sizeof(edges[0])) {
            left[i] = edges[i];
            right[i] = edges[(i + 3) % (sizeof(edges) / sizeof(edges[0]))];
        } else {
            left[i] = next_random(&random_state);
            right[i] = next_random(&random_state);
        }
    }
}

static uint64_t reference_poly(const uint64_t *data, size_t n) {
    uint64_t states[LANE_COUNT];
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        states[lane] = UINT64_C(0x243f6a8885a308d3) +
            ((uint64_t)lane + 1) * UINT64_C(0x9e3779b97f4a7c15);
    }
    for (size_t i = 0; i < n; ++i) {
        uint64_t word = data[i] + (uint64_t)i;
        for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
            states[lane] = states[lane] * UINT64_C(0x100000001b3) + word +
                (uint64_t)lane * UINT64_C(0x9e3779b1);
        }
    }
    uint64_t result = 0;
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        result ^= states[lane] + (uint64_t)lane * UINT64_C(0xd6e8feb86659fd93);
    }
    return result;
}

static uint64_t rotate_left(uint64_t value, unsigned shift) {
    return (value << shift) | (value >> (64 - shift));
}

static uint64_t reference_hash(const uint64_t *data, size_t n) {
    uint64_t states[LANE_COUNT];
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        states[lane] = UINT64_C(0x13198a2e03707344) +
            ((uint64_t)lane + 1) * UINT64_C(0x9e3779b97f4a7c15);
    }
    for (size_t i = 0; i < n; ++i) {
        uint64_t word = data[i] ^ ((uint64_t)i * UINT64_C(0x94d049bb133111eb));
        for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
            uint64_t salt = word + ((uint64_t)lane + 1) * UINT64_C(0xa0761d6478bd642f);
            states[lane] = (rotate_left(states[lane], (unsigned)lane + 1) ^ salt) *
                UINT64_C(0xe7037ed1a0b428db);
        }
    }
    uint64_t result = 0;
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        result ^= states[lane] + (uint64_t)lane * UINT64_C(0xd6e8feb86659fd93);
    }
    return result;
}

static uint64_t reference_dot(const uint64_t *left, const uint64_t *right, size_t n) {
    uint64_t states[LANE_COUNT];
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        states[lane] = UINT64_C(0xa4093822299f31d0) +
            ((uint64_t)lane + 1) * UINT64_C(0x9e3779b97f4a7c15);
    }
    for (size_t i = 0; i < n; ++i) {
        uint64_t pair = left[i] * right[i];
        for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
            states[lane] += pair * ((uint64_t)lane + 1) * UINT64_C(0x9e3779b97f4a7c15);
        }
    }
    uint64_t result = 0;
    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {
        result ^= states[lane] + (uint64_t)lane * UINT64_C(0xd6e8feb86659fd93);
    }
    return result;
}

static int check_case(size_t n, uint64_t seed) {
    uint64_t left[VECTOR_CAPACITY];
    uint64_t right[VECTOR_CAPACITY];
    fill_inputs(left, right, n, seed);
    const uint64_t expected_poly = reference_poly(left, n);
    const uint64_t expected_hash = reference_hash(left, n);
    const uint64_t expected_dot = reference_dot(left, right, n);
    if (pressure_poly(left, n) != expected_poly ||
        pressure_hash(left, n) != expected_hash ||
        pressure_dot(left, right, n) != expected_dot) {
        fprintf(stderr, "kernel mismatch for n=%zu seed=%" PRIu64 "\n", n, seed);
        return 0;
    }
    return 1;
}

static uint64_t elapsed_ns(struct timespec start, struct timespec end) {
    return (uint64_t)(end.tv_sec - start.tv_sec) * UINT64_C(1000000000) +
        (uint64_t)(end.tv_nsec - start.tv_nsec);
}

static int parse_iterations(int argc, char **argv, uint64_t *iterations) {
    if (argc == 1) {
        *iterations = 1000;
        return 1;
    }
    if (argc != 2 || argv[1][0] == '\0' || argv[1][0] == '-') {
        return 0;
    }
    errno = 0;
    char *end = NULL;
    unsigned long long parsed = strtoull(argv[1], &end, 10);
    if (errno != 0 || end == argv[1] || *end != '\0' || parsed == 0) {
        return 0;
    }
    *iterations = (uint64_t)parsed;
    return 1;
}

int main(int argc, char **argv) {
    uint64_t iterations;
    if (!parse_iterations(argc, argv, &iterations)) {
        fprintf(stderr, "usage: %s [positive-iterations]\n", argv[0]);
        return 2;
    }

    static const size_t lengths[] = {0, 1, 2, 7, 17, 63, VECTOR_CAPACITY};
    for (size_t i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i) {
        if (!check_case(lengths[i], UINT64_C(0x123456789abcdef0) + (uint64_t)i)) {
            return 1;
        }
    }

    uint64_t left[VECTOR_CAPACITY];
    uint64_t right[VECTOR_CAPACITY];
    fill_inputs(left, right, VECTOR_CAPACITY, UINT64_C(0xfeedfacecafebeef));
    uint64_t checksum = UINT64_C(0x6a09e667f3bcc909);
    struct timespec start;
    struct timespec end;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        perror("clock_gettime");
        return 1;
    }
    for (uint64_t i = 0; i < iterations; ++i) {
        checksum += pressure_poly(left, VECTOR_CAPACITY);
        checksum ^= pressure_hash(right, VECTOR_CAPACITY);
        checksum += pressure_dot(left, right, VECTOR_CAPACITY) ^ i;
    }
    if (clock_gettime(CLOCK_MONOTONIC, &end) != 0) {
        perror("clock_gettime");
        return 1;
    }
    printf("{\"correct\":true,\"iterations\":%" PRIu64 ",\"checksum\":\"%016" PRIx64 "\",\"elapsed_ns\":%" PRIu64 "}\n",
           iterations, checksum, elapsed_ns(start, end));
    return 0;
}
