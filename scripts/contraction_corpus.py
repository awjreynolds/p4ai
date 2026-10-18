"""Generate small, checked integer tensor contractions for loop-order trials.

Eligibility is deliberately narrow: dense, fixed-shape ``uint32`` matrices;
non-overlapping input/output buffers; and one complete ``m × n × k`` product.
Unsigned accumulation is modulo 2**32, so all six loop permutations preserve
the result. There are no floating-point reassociation, sparse-layout, aliasing,
or concurrent-update questions in this corpus.
"""

from __future__ import annotations

from pathlib import Path

ORDERS = ("ijk", "ikj", "jik", "jki", "kij", "kji")
SPEC_KEYS = {
    "m",
    "n",
    "k",
    "a_column_major",
    "b_column_major",
    "c_column_major",
}
MAX_DIMENSION = 4096
MAX_MULTIPLIES = 1 << 28


def generate_case(
    output_dir: Path, spec: dict[str, object], order: str = "ijk"
) -> dict[str, object]:
    """Write a kernel/independent-driver pair and source-derived numeric features.

    ``spec`` names matrix dimensions and physical row/column layout. ``order``
    is the exact loop nesting to emit; it is kept out of workload features so
    callers can describe it as a selectable action.
    """
    dimensions, layouts = _validate(spec, order)
    m, n, k = dimensions["m"], dimensions["n"], dimensions["k"]
    strides = _strides(dimensions, layouts)
    layout_tag = "".join(
        f"{name[0]}{'c' if layouts[name] else 'r'}"
        for name in ("a_column_major", "b_column_major", "c_column_major")
    )
    stem = f"contract_{m}_{n}_{k}_{layout_tag}_{order}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    kernel_path = output_dir / f"{stem}_kernel.c"
    driver_path = output_dir / f"{stem}_driver.c"
    kernel_path.write_text(_kernel_source(dimensions, strides, order), encoding="utf-8")
    driver_path.write_text(_driver_source(dimensions, layouts), encoding="utf-8")
    return {
        "kernel": kernel_path,
        "driver": driver_path,
        "features": _features(dimensions, layouts, strides),
    }


def _validate(
    spec: dict[str, object], order: str
) -> tuple[dict[str, int], dict[str, bool]]:
    if not isinstance(spec, dict) or set(spec) != SPEC_KEYS:
        required = ", ".join(sorted(SPEC_KEYS))
        raise ValueError(f"spec must contain exactly these keys: {required}")
    if order not in ORDERS:
        raise ValueError(f"order must be one of {', '.join(ORDERS)}")

    dimensions: dict[str, int] = {}
    for name in ("m", "n", "k"):
        value = spec[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= MAX_DIMENSION
        ):
            raise ValueError(
                f"{name} must be an integer from 1 through {MAX_DIMENSION}"
            )
        dimensions[name] = value
    if dimensions["m"] * dimensions["n"] * dimensions["k"] > MAX_MULTIPLIES:
        raise ValueError(f"m*n*k must not exceed {MAX_MULTIPLIES}")

    layouts: dict[str, bool] = {}
    for name in ("a_column_major", "b_column_major", "c_column_major"):
        value = spec[name]
        if type(value) is not bool:
            raise ValueError(f"{name} must be a bool")
        layouts[name] = value
    return dimensions, layouts


def _strides(dimensions: dict[str, int], layouts: dict[str, bool]) -> dict[str, int]:
    m, n, k = dimensions["m"], dimensions["n"], dimensions["k"]
    return {
        "a_i_stride": 1 if layouts["a_column_major"] else k,
        "a_k_stride": m if layouts["a_column_major"] else 1,
        "b_k_stride": 1 if layouts["b_column_major"] else n,
        "b_j_stride": k if layouts["b_column_major"] else 1,
        "c_i_stride": 1 if layouts["c_column_major"] else n,
        "c_j_stride": m if layouts["c_column_major"] else 1,
    }


def _features(
    dimensions: dict[str, int],
    layouts: dict[str, bool],
    strides: dict[str, int],
) -> dict[str, int]:
    return {
        **dimensions,
        **{name: int(value) for name, value in layouts.items()},
        **strides,
        "trip_count": dimensions["m"] * dimensions["n"] * dimensions["k"],
        "loads_per_product": 3,
        "stores_per_product": 1,
        "multiplies_per_product": 1,
        "additions_per_product": 1,
        "element_bits": 32,
        "arithmetic_bits": 32,
    }


def _index_expression(
    i: str, j: str, k: str, strides: dict[str, int]
) -> tuple[str, str, str]:
    return (
        f"({i}) * {strides['a_i_stride']}u + ({k}) * {strides['a_k_stride']}u",
        f"({k}) * {strides['b_k_stride']}u + ({j}) * {strides['b_j_stride']}u",
        f"({i}) * {strides['c_i_stride']}u + ({j}) * {strides['c_j_stride']}u",
    )


def _kernel_source(
    dimensions: dict[str, int],
    strides: dict[str, int],
    order: str,
) -> str:
    m, n, k = dimensions["m"], dimensions["n"], dimensions["k"]
    a_index, b_index, c_index = _index_expression("i", "j", "k", strides)
    lines = [
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        f"#define M {m}u",
        f"#define N {n}u",
        f"#define K {k}u",
        f"#define A_COUNT {m * k}u",
        f"#define B_COUNT {k * n}u",
        f"#define C_COUNT {m * n}u",
        "",
        "uint64_t harness_kernel(const uint32_t *restrict a,",
        "                        const uint32_t *restrict b,",
        "                        uint32_t *restrict out, uint64_t salt) {",
        "    for (size_t cell = 0; cell < C_COUNT; ++cell) {",
        "        out[cell] = 0;",
        "    }",
        f"    // CONTRACTION_LOOP_ORDER: {order}",
    ]
    for depth, variable in enumerate(order):
        bound = {"i": "M", "j": "N", "k": "K"}[variable]
        lines.append(
            f"    {'    ' * depth}for (size_t {variable} = 0; {variable} < {bound}; ++{variable}) {{"
        )
    indent = "    " * (len(order) + 1)
    lines.extend(
        [
            f"{indent}const uint32_t left = a[(size_t)({a_index})] ^ (uint32_t)salt;",
            f"{indent}const uint32_t right = b[(size_t)({b_index})];",
            f"{indent}out[(size_t)({c_index})] += left * right;",
        ]
    )
    for depth in reversed(range(len(order))):
        lines.append(f"{'    ' * (depth + 1)}}}")
    lines.extend(
        [
            f"    return out[(size_t)({m // 2}u * {strides['c_i_stride']}u + {n // 2}u * {strides['c_j_stride']}u)];",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _driver_source(
    dimensions: dict[str, int],
    layouts: dict[str, bool],
) -> str:
    m, n, k = dimensions["m"], dimensions["n"], dimensions["k"]
    a_count, b_count, c_count = m * k, k * n, m * n
    # The reference uses its own row/column formulae and reverse traversal;
    # it does not use the kernel's generated index expression or loop order.
    if layouts["a_column_major"]:
        ref_a = "(size_t)row + (size_t)inner * M"
    else:
        ref_a = "(size_t)row * K + (size_t)inner"
    if layouts["b_column_major"]:
        ref_b = "(size_t)inner + (size_t)column * K"
    else:
        ref_b = "(size_t)inner * N + (size_t)column"
    if layouts["c_column_major"]:
        ref_c = "(size_t)row + (size_t)column * M"
    else:
        ref_c = "(size_t)row * N + (size_t)column"

    return "\n".join(
        [
            "#define _POSIX_C_SOURCE 200809L",
            "#include <errno.h>",
            "#include <inttypes.h>",
            "#include <stdint.h>",
            "#include <stdio.h>",
            "#include <stdlib.h>",
            "#include <time.h>",
            "",
            f"#define M {m}u",
            f"#define N {n}u",
            f"#define K {k}u",
            f"#define A_COUNT {a_count}u",
            f"#define B_COUNT {b_count}u",
            f"#define C_COUNT {c_count}u",
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
            "static void fill_inputs(uint32_t *a, uint32_t *b, uint64_t seed) {",
            "    static const uint32_t edges[] = {",
            "        UINT32_C(0), UINT32_C(1), UINT32_C(2), UINT32_C(3),",
            "        UINT32_C(0x7fffffff), UINT32_C(0x80000000),",
            "        UINT32_C(0xfffffffe), UINT32_MAX,",
            "    };",
            "    uint64_t state = seed;",
            "    for (size_t p = 0; p < A_COUNT; ++p) {",
            "        if (p < sizeof(edges) / sizeof(edges[0])) {",
            "            a[p] = edges[(p + (size_t)(seed & UINT64_C(7))) & 7u];",
            "        } else {",
            "            a[p] = (uint32_t)next_random(&state);",
            "        }",
            "    }",
            "    for (size_t p = 0; p < B_COUNT; ++p) {",
            "        if (p < sizeof(edges) / sizeof(edges[0])) {",
            "            b[p] = edges[(p + 3u + (size_t)(seed & UINT64_C(7))) & 7u];",
            "        } else {",
            "            b[p] = (uint32_t)next_random(&state);",
            "        }",
            "    }",
            "}",
            "",
            "static int check_case(const uint32_t *a, const uint32_t *b,",
            "                      uint32_t *actual, uint32_t *expected,",
            "                      uint64_t salt) {",
            "    for (size_t p = 0; p < C_COUNT; ++p) {",
            "        actual[p] = UINT32_C(0xa5a5a5a5);",
            "    }",
            "    for (size_t column_cursor = N; column_cursor > 0; --column_cursor) {",
            "        const size_t column = column_cursor - 1u;",
            "        for (size_t row_cursor = M; row_cursor > 0; --row_cursor) {",
            "            const size_t row = row_cursor - 1u;",
            "            uint32_t sum = 0;",
            "            for (size_t inner_cursor = K; inner_cursor > 0; --inner_cursor) {",
            "                const size_t inner = inner_cursor - 1u;",
            f"                const uint32_t x = a[{ref_a}] ^ (uint32_t)salt;",
            f"                const uint32_t y = b[{ref_b}];",
            "                sum += (uint32_t)((uint64_t)x * (uint64_t)y);",
            "            }",
            f"            expected[{ref_c}] = sum;",
            "        }",
            "    }",
            "    (void)harness_kernel(a, b, actual, salt);",
            "    for (size_t p = 0; p < C_COUNT; ++p) {",
            "        if (actual[p] != expected[p]) {",
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
            "        parsed == (unsigned long long)UINT64_MAX) {",
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
            "    uint32_t *a = malloc((size_t)A_COUNT * sizeof(*a));",
            "    uint32_t *b = malloc((size_t)B_COUNT * sizeof(*b));",
            "    uint32_t *actual = malloc((size_t)C_COUNT * sizeof(*actual));",
            "    uint32_t *expected = malloc((size_t)C_COUNT * sizeof(*expected));",
            "    uint32_t *out = malloc((size_t)C_COUNT * sizeof(*out));",
            "    if (a == NULL || b == NULL || actual == NULL || expected == NULL || out == NULL) {",
            '        fprintf(stderr, "allocation failed\\n");',
            "        free(a); free(b); free(actual); free(expected); free(out);",
            "        return 1;",
            "    }",
            "    const uint64_t seeds[] = {",
            "        UINT64_C(0x123456789abcdef0), UINT64_C(0xfeedfacecafebeef)",
            "    };",
            "    const uint64_t salts[] = {",
            "        UINT64_C(0xfedcba9876543210), UINT64_C(0x0123456789abcdef)",
            "    };",
            "    for (size_t trial = 0; trial < 2; ++trial) {",
            "        fill_inputs(a, b, seeds[trial]);",
            "        if (!check_case(a, b, actual, expected, salts[trial])) {",
            '            fprintf(stderr, "kernel mismatch\\n");',
            "            free(a); free(b); free(actual); free(expected); free(out);",
            "            return 1;",
            "        }",
            "    }",
            "    fill_inputs(a, b, UINT64_C(0x3141592653589793));",
            "    uint64_t checksum = UINT64_C(0x6a09e667f3bcc909);",
            "    struct timespec start;",
            "    struct timespec end;",
            "    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {",
            '        perror("clock_gettime");',
            "        free(a); free(b); free(actual); free(expected); free(out);",
            "        return 1;",
            "    }",
            "    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {",
            "        const uint64_t result = harness_kernel(a, b, out, iteration + UINT64_C(1));",
            "        checksum = (checksum ^ result) * UINT64_C(0x9e3779b97f4a7c15) + iteration;",
            "    }",
            "    if (clock_gettime(CLOCK_MONOTONIC, &end) != 0) {",
            '        perror("clock_gettime");',
            "        free(a); free(b); free(actual); free(expected); free(out);",
            "        return 1;",
            "    }",
            '    printf("{\\"correct\\":true,\\"iterations\\":%llu,\\"checksum\\":\\"%016llx\\",\\"elapsed_ns\\":%llu}\\n",',
            "           (unsigned long long)iterations, (unsigned long long)checksum,",
            "           (unsigned long long)elapsed_ns(start, end));",
            "    free(a); free(b); free(actual); free(expected); free(out);",
            "    return 0;",
            "}",
            "",
        ]
    )
