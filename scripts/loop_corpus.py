"""Generate fixed-trip-count integer loops for native vectorization experiments."""

from __future__ import annotations

from pathlib import Path

FAMILIES = (
    "affine",
    "reduction",
    "dot_product",
    "polynomial32",
    "hash64",
    "conditional",
    "stride",
    "stencil",
)
MIN_N = 8
MAX_N = 32768

_HASH_MULTIPLIER_1 = "UINT64_C(0xbf58476d1ce4e5b9)"
_HASH_MULTIPLIER_2 = "UINT64_C(0x94d049bb133111eb)"


def generate_case(output_dir: Path, family: str, n: int) -> dict[str, object]:
    """Write a kernel/driver pair and source-level numeric features.

    ``n`` is embedded in both translation units, so every compiler policy sees
    the same constant loop bound. ``features`` describes operations in the
    target loop body; it contains no benchmark measurements or family label.
    """
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {', '.join(FAMILIES)}")
    if isinstance(n, bool) or not isinstance(n, int) or not MIN_N <= n <= MAX_N:
        raise ValueError(f"n must be an integer from {MIN_N} through {MAX_N}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"loop_{family}_{n}"
    kernel_path = output_dir / f"{stem}_kernel.c"
    driver_path = output_dir / f"{stem}_driver.c"
    kernel_path.write_text(_kernel_source(family, n), encoding="utf-8")
    driver_path.write_text(_driver_source(family, n), encoding="utf-8")
    return {
        "kernel": kernel_path,
        "driver": driver_path,
        "features": _features(family, n),
    }


def _features(family: str, n: int) -> dict[str, int]:
    features = {
        "element_bits": 32,
        "arithmetic_bits": 32,
        "trip_count": n,
        "loads": 2,
        "stores": 1,
        "multiplies": 0,
        "shifts": 0,
        "branches": 0,
        "reduction": 0,
        "stride": 1,
    }
    if family == "reduction":
        features.update(stores=0, arithmetic_bits=64, reduction=1)
    elif family == "dot_product":
        features.update(stores=0, arithmetic_bits=64, multiplies=1, reduction=1)
    elif family == "polynomial32":
        features.update(loads=1, multiplies=1)
    elif family == "hash64":
        features.update(arithmetic_bits=64, multiplies=2, shifts=4)
    elif family == "conditional":
        features.update(branches=1)
    elif family == "stride":
        features.update(multiplies=1, stride=2)
    elif family == "stencil":
        features.update(trip_count=n - 2, loads=4)
    elif family == "affine":
        features.update(multiplies=1)
    return features


def _kernel_source(family: str, n: int) -> str:
    lines = [
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        f"#define LOOP_N {n}u",
        "",
        "uint64_t harness_kernel(const uint32_t *restrict a,",
        "                        const uint32_t *restrict b,",
        "                        uint32_t *restrict out, uint64_t salt) {",
    ]
    if family in {"reduction", "dot_product"}:
        lines.append("    uint64_t sum = 0;")

    lines.extend(_kernel_body(family))
    if family in {"reduction", "dot_product"}:
        lines.extend(["    return sum;", "}", ""])
    else:
        lines.extend(["    return out[LOOP_N / 2u];", "}", ""])
    return "\n".join(lines)


def _kernel_body(family: str) -> list[str]:
    if family == "affine":
        body = [
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        out[i] = left * UINT32_C(3) + right + (uint32_t)salt;",
            "    }",
        ]
    elif family == "reduction":
        body = [
            "    (void)out;",
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        sum += (uint64_t)(left + right + (uint32_t)salt);",
            "    }",
        ]
    elif family == "dot_product":
        body = [
            "    (void)out;",
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        sum += (uint64_t)left * (uint64_t)right + salt;",
            "    }",
        ]
    elif family == "polynomial32":
        body = [
            "    (void)b;",
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t value = a[i] ^ (uint32_t)salt;",
            "        out[i] = value * UINT32_C(33) + UINT32_C(17);",
            "    }",
        ]
    elif family == "hash64":
        body = [
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        uint64_t value = (uint64_t)left | ((uint64_t)right << 32);",
            "        value ^= salt;",
            f"        value = (value ^ (value >> 29)) * {_HASH_MULTIPLIER_1};",
            f"        value = (value ^ (value >> 31)) * {_HASH_MULTIPLIER_2};",
            "        out[i] = (uint32_t)(value ^ (value >> 32));",
            "    }",
        ]
    elif family == "conditional":
        body = [
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        if (((left ^ (uint32_t)salt) & UINT32_C(1)) != 0) {",
            "            out[i] = left + right;",
            "        } else {",
            "            out[i] = left - right;",
            "        }",
            "    }",
        ]
    elif family == "stride":
        body = [
            "    // VECTOR_LOOP",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        const uint32_t left = a[2u * i];",
            "        const uint32_t right = b[2u * i + 1u];",
            "        out[i] = left * UINT32_C(5) + right + (uint32_t)salt;",
            "    }",
        ]
    else:  # stencil
        body = [
            "    // VECTOR_LOOP",
            "    for (size_t i = 1; i < LOOP_N - 1u; ++i) {",
            "        const uint32_t left = a[i - 1u];",
            "        const uint32_t center = a[i];",
            "        const uint32_t right = a[i + 1u];",
            "        const uint32_t other = b[i];",
            "        out[i] = left + center + right + (other ^ (uint32_t)salt);",
            "    }",
        ]
    return body


def _driver_source(family: str, n: int) -> str:
    input_count = n * 2
    lines = [
        "#define _POSIX_C_SOURCE 200809L",
        "#include <errno.h>",
        "#include <inttypes.h>",
        "#include <stdint.h>",
        "#include <stdio.h>",
        "#include <stdlib.h>",
        "#include <time.h>",
        "",
        f"#define LOOP_N {n}u",
        f"#define INPUT_COUNT {input_count}u",
        "",
        "uint64_t harness_kernel(const uint32_t *a, const uint32_t *b,",
        "                        uint32_t *out, uint64_t salt);",
        "",
        "static uint64_t next_random(uint64_t *state) {",
        "    uint64_t value = (*state += UINT64_C(0x9e3779b97f4a7c15));",
        "    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);",
        "    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);",
        "    return value ^ (value >> 31);",
        "}",
        "",
        "static void fill_inputs(uint32_t *a, uint32_t *b, size_t count, uint64_t seed) {",
        "    static const uint32_t edges[] = {",
        "        UINT32_C(0), UINT32_C(1), UINT32_C(2), UINT32_C(3),",
        "        UINT32_C(0x7fffffff), UINT32_C(0x80000000),",
        "        UINT32_C(0xfffffffe), UINT32_MAX,",
        "    };",
        "    uint64_t state = seed;",
        "    for (size_t i = 0; i < count; ++i) {",
        "        if (i < sizeof(edges) / sizeof(edges[0])) {",
        "            a[i] = edges[(i + (size_t)(seed & UINT64_C(7))) & 7u];",
        "            b[i] = edges[(i + 3u + (size_t)(seed & UINT64_C(7))) & 7u];",
        "        } else {",
        "            a[i] = (uint32_t)next_random(&state);",
        "            b[i] = (uint32_t)next_random(&state);",
        "        }",
        "    }",
        "}",
        "",
        "static void fill_output(uint32_t *out) {",
        "    for (size_t i = 0; i < LOOP_N; ++i) {",
        "        out[i] = UINT32_C(0xa5a5a5a5);",
        "    }",
        "}",
        "",
    ]
    if family == "hash64":
        lines.extend(
            [
                "static uint32_t reference_hash(uint32_t left, uint32_t right, uint64_t salt) {",
                "    uint64_t value = ((uint64_t)right << 32) + (uint64_t)left;",
                "    value ^= salt;",
                f"    const uint64_t first = (value ^ (value >> 29)) * {_HASH_MULTIPLIER_1};",
                f"    const uint64_t second = (first ^ (first >> 31)) * {_HASH_MULTIPLIER_2};",
                "    const uint32_t low = (uint32_t)second;",
                "    const uint32_t high = (uint32_t)(second >> 32);",
                "    return low ^ high;",
                "}",
                "",
            ]
        )
    lines.extend(
        [
            "static uint64_t reference_kernel(const uint32_t *a, const uint32_t *b,",
            "                                 uint32_t *out, uint64_t salt) {",
        ]
    )
    if family in {"reduction", "dot_product"}:
        lines.append("    uint64_t sum = 0;")
    lines.extend(_reference_body(family))
    if family in {"reduction", "dot_product"}:
        lines.extend(["    return sum;", "}"])
    else:
        lines.extend(["    return out[LOOP_N / 2u];", "}"])
    lines.extend(
        [
            "",
            "static int check_case(uint64_t seed, uint64_t salt) {",
            "    uint32_t a[INPUT_COUNT];",
            "    uint32_t b[INPUT_COUNT];",
            "    uint32_t actual[LOOP_N];",
            "    uint32_t expected[LOOP_N];",
            "    fill_inputs(a, b, INPUT_COUNT, seed);",
            "    fill_output(actual);",
            "    fill_output(expected);",
            "    const uint64_t expected_result = reference_kernel(a, b, expected, salt);",
            "    const uint64_t actual_result = harness_kernel(a, b, actual, salt);",
            "    if (expected_result != actual_result) {",
            "        return 0;",
            "    }",
            "    for (size_t i = 0; i < LOOP_N; ++i) {",
            "        if (expected[i] != actual[i]) {",
            "            return 0;",
            "        }",
            "    }",
            "    return 1;",
            "}",
            "",
            "static uint64_t elapsed_ns(struct timespec start, struct timespec end) {",
            "    int64_t seconds = (int64_t)(end.tv_sec - start.tv_sec);",
            "    int64_t nanoseconds = (int64_t)(end.tv_nsec - start.tv_nsec);",
            "    if (nanoseconds < 0) {",
            "        --seconds;",
            "        nanoseconds += INT64_C(1000000000);",
            "    }",
            "    return (uint64_t)seconds * UINT64_C(1000000000) + (uint64_t)nanoseconds;",
            "}",
            "",
            "static int parse_iterations(int argc, char **argv, uint64_t *iterations) {",
            "    if (argc != 2 || argv[1][0] == '\\0' || argv[1][0] == '-') {",
            "        return 0;",
            "    }",
            "    errno = 0;",
            "    char *end = NULL;",
            "    unsigned long long parsed = strtoull(argv[1], &end, 10);",
            "    if (errno != 0 || end == argv[1] || *end != '\\0' || parsed == 0 ||",
            "        parsed > (unsigned long long)UINT64_MAX) {",
            "        return 0;",
            "    }",
            "    *iterations = (uint64_t)parsed;",
            "    return 1;",
            "}",
            "",
            "int main(int argc, char **argv) {",
            "    uint64_t iterations;",
            "    if (!parse_iterations(argc, argv, &iterations)) {",
            '        fprintf(stderr, "usage: %s positive-iterations\\n", argv[0]);',
            "        return 2;",
            "    }",
            "    if (!check_case(UINT64_C(0x123456789abcdef0), UINT64_C(0xfedcba9876543210)) ||",
            "        !check_case(UINT64_C(0xfeedfacecafebeef), UINT64_C(0x0123456789abcdef))) {",
            '        fprintf(stderr, "kernel mismatch\\n");',
            "        return 1;",
            "    }",
            "    uint32_t a[INPUT_COUNT];",
            "    uint32_t b[INPUT_COUNT];",
            "    uint32_t out[LOOP_N];",
            "    fill_inputs(a, b, INPUT_COUNT, UINT64_C(0x3141592653589793));",
            "    fill_output(out);",
            "    uint64_t checksum = UINT64_C(0x6a09e667f3bcc909);",
            "    struct timespec start;",
            "    struct timespec end;",
            "    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {",
            '        perror("clock_gettime");',
            "        return 1;",
            "    }",
            "    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {",
            "        const uint64_t result = harness_kernel(a, b, out, iteration + UINT64_C(1));",
            "        checksum = (checksum ^ result) * UINT64_C(0x9e3779b97f4a7c15) + iteration;",
            "    }",
            "    if (clock_gettime(CLOCK_MONOTONIC, &end) != 0) {",
            '        perror("clock_gettime");',
            "        return 1;",
            "    }",
            '    printf("{\\"correct\\":true,\\"iterations\\":%llu,\\"checksum\\":\\"%016llx\\",\\"elapsed_ns\\":%llu}\\n",',
            "           (unsigned long long)iterations, (unsigned long long)checksum,",
            "           (unsigned long long)elapsed_ns(start, end));",
            "    return 0;",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _reference_body(family: str) -> list[str]:
    if family == "affine":
        return [
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        uint32_t value = left;",
            "        value += left;",
            "        value += left;",
            "        value += right;",
            "        value += (uint32_t)salt;",
            "        out[i] = value;",
            "    }",
        ]
    if family == "reduction":
        return [
            "    (void)out;",
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        uint32_t term = left;",
            "        term += right;",
            "        term += (uint32_t)salt;",
            "        sum += term;",
            "    }",
        ]
    if family == "dot_product":
        return [
            "    (void)out;",
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        const uint64_t product = (uint64_t)left * (uint64_t)right;",
            "        sum += product;",
            "        sum += salt;",
            "    }",
        ]
    if family == "polynomial32":
        return [
            "    (void)b;",
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t value = a[i] ^ (uint32_t)salt;",
            "        out[i] = (value << 5) + value + UINT32_C(17);",
            "    }",
        ]
    if family == "hash64":
        return [
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        out[i] = reference_hash(left, right, salt);",
            "    }",
        ]
    if family == "conditional":
        return [
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[i];",
            "        const uint32_t right = b[i];",
            "        const uint32_t choose_add = UINT32_C(0) -",
            "            ((left ^ (uint32_t)salt) & UINT32_C(1));",
            "        const uint32_t added = left + right;",
            "        const uint32_t subtracted = left - right;",
            "        out[i] = (added & choose_add) | (subtracted & ~choose_add);",
            "    }",
        ]
    if family == "stride":
        return [
            "    for (size_t cursor = LOOP_N; cursor > 0u; --cursor) {",
            "        const size_t i = cursor - 1u;",
            "        const uint32_t left = a[2u * i];",
            "        const uint32_t right = b[2u * i + 1u];",
            "        out[i] = (left << 2) + left + right + (uint32_t)salt;",
            "    }",
        ]
    return [
        "    for (size_t cursor = LOOP_N - 1u; cursor > 1u; --cursor) {",
        "        const size_t i = cursor - 1u;",
        "        const uint32_t left = a[i - 1u];",
        "        const uint32_t center = a[i];",
        "        const uint32_t right = a[i + 1u];",
        "        const uint32_t other = b[i];",
        "        out[i] = (other ^ (uint32_t)salt) + right + center + left;",
        "    }",
    ]
