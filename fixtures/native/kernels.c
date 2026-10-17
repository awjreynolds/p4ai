#include "native_corpus.h"

#define APPLY_LANES(OP, value) \
    OP(a00, value, 0) OP(a01, value, 1) OP(a02, value, 2) OP(a03, value, 3) \
    OP(a04, value, 4) OP(a05, value, 5) OP(a06, value, 6) OP(a07, value, 7) \
    OP(a08, value, 8) OP(a09, value, 9) OP(a10, value, 10) OP(a11, value, 11) \
    OP(a12, value, 12) OP(a13, value, 13) OP(a14, value, 14) OP(a15, value, 15) \
    OP(a16, value, 16) OP(a17, value, 17) OP(a18, value, 18) OP(a19, value, 19) \
    OP(a20, value, 20) OP(a21, value, 21) OP(a22, value, 22) OP(a23, value, 23) \
    OP(a24, value, 24) OP(a25, value, 25) OP(a26, value, 26) OP(a27, value, 27) \
    OP(a28, value, 28) OP(a29, value, 29) OP(a30, value, 30) OP(a31, value, 31) \
    OP(a32, value, 32) OP(a33, value, 33) OP(a34, value, 34) OP(a35, value, 35) \
    OP(a36, value, 36) OP(a37, value, 37) OP(a38, value, 38) OP(a39, value, 39)

#define DECLARE_STATE(acc, seed, lane) \
    uint64_t acc = (seed) + ((uint64_t)(lane) + UINT64_C(1)) * UINT64_C(0x9e3779b97f4a7c15);
#define FOLD_STATE(acc, result, lane) \
    (result) ^= (acc) + ((uint64_t)(lane) * UINT64_C(0xd6e8feb86659fd93));

#define POLY_STEP(acc, word, lane) \
    (acc) = (acc) * UINT64_C(0x100000001b3) + (word) + ((uint64_t)(lane) * UINT64_C(0x9e3779b1));

#define HASH_STEP(acc, word, lane) \
    (acc) = ((((acc) << ((lane) + 1)) | ((acc) >> (63 - (lane)))) ^ \
             ((word) + ((uint64_t)(lane) + UINT64_C(1)) * UINT64_C(0xa0761d6478bd642f))) * \
            UINT64_C(0xe7037ed1a0b428db);

#define DOT_STEP(acc, pair, lane) \
    (acc) += (pair) * ((uint64_t)(lane) + UINT64_C(1)) * UINT64_C(0x9e3779b97f4a7c15);

uint64_t pressure_poly(const uint64_t *data, size_t n) {
    const uint64_t seed = UINT64_C(0x243f6a8885a308d3);
    APPLY_LANES(DECLARE_STATE, seed)
    for (size_t i = 0; i < n; ++i) {
        const uint64_t word = data[i] + (uint64_t)i;
        APPLY_LANES(POLY_STEP, word)
    }
    uint64_t result = 0;
    APPLY_LANES(FOLD_STATE, result)
    return result;
}

uint64_t pressure_hash(const uint64_t *data, size_t n) {
    const uint64_t seed = UINT64_C(0x13198a2e03707344);
    APPLY_LANES(DECLARE_STATE, seed)
    for (size_t i = 0; i < n; ++i) {
        const uint64_t word = data[i] ^ ((uint64_t)i * UINT64_C(0x94d049bb133111eb));
        APPLY_LANES(HASH_STEP, word)
    }
    uint64_t result = 0;
    APPLY_LANES(FOLD_STATE, result)
    return result;
}

uint64_t pressure_dot(const uint64_t *left, const uint64_t *right, size_t n) {
    const uint64_t seed = UINT64_C(0xa4093822299f31d0);
    APPLY_LANES(DECLARE_STATE, seed)
    for (size_t i = 0; i < n; ++i) {
        const uint64_t pair = left[i] * right[i];
        APPLY_LANES(DOT_STEP, pair)
    }
    uint64_t result = 0;
    APPLY_LANES(FOLD_STATE, result)
    return result;
}
