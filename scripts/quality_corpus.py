"""Generate standalone native workloads for register-pressure experiments."""

from __future__ import annotations

from pathlib import Path

FAMILIES = (
    "polynomial",
    "rotate_xor",
    "cross_lane",
    "dot_product",
    "masked_recurrence",
    "alternating_inputs",
)
MAX_LANES = 64
MAX_ROUNDS = 64

_SEED = "UINT64_C(0x243f6a8885a308d3)"
_LANE_SEED = "UINT64_C(0x9e3779b97f4a7c15)"
_FOLD = "UINT64_C(0xd6e8feb86659fd93)"
_INDEX_MIX = "UINT64_C(0x94d049bb133111eb)"
_ROUND_MIX = "UINT64_C(0xa0761d6478bd642f)"
_POLY_MULTIPLIER = "UINT64_C(0x100000001b3)"
_ROTATE_MULTIPLIER = "UINT64_C(0xe7037ed1a0b428db)"
_CROSS_MULTIPLIER = "UINT64_C(0xbf58476d1ce4e5b9)"
_ALT_MULTIPLIER = "UINT64_C(0x9e3779b185ebca87)"


def generate_case(
    output_dir: Path,
    family: str,
    lanes: int,
    rounds: int = 4,
) -> dict[str, Path]:
    """Write a kernel/driver pair for one deterministic workload.

    The kernel keeps ``lanes`` scalar recurrence values live. ``rounds``
    controls fixed work per input word. Both values are bounded to keep
    generated source sizes predictable while covering useful pressure levels.
    """
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {', '.join(FAMILIES)}")
    if (
        isinstance(lanes, bool)
        or not isinstance(lanes, int)
        or not 1 <= lanes <= MAX_LANES
    ):
        raise ValueError(f"lanes must be an integer from 1 through {MAX_LANES}")
    if (
        isinstance(rounds, bool)
        or not isinstance(rounds, int)
        or not 1 <= rounds <= MAX_ROUNDS
    ):
        raise ValueError(f"rounds must be an integer from 1 through {MAX_ROUNDS}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"quality_{family}_{lanes}lanes_{rounds}rounds"
    kernel_path = output_dir / f"{stem}_kernel.c"
    driver_path = output_dir / f"{stem}_driver.c"
    kernel_path.write_text(_kernel_source(family, lanes, rounds), encoding="utf-8")
    driver_path.write_text(_driver_source(family, lanes, rounds), encoding="utf-8")
    return {"kernel": kernel_path, "driver": driver_path}


def _state_name(lane: int) -> str:
    return f"state_{lane:02d}"


def _lane_shift(lane: int) -> int:
    # Keep rotate counts in [1, 63], so both C shifts are defined.
    return lane % 63 + 1


def _rotate(value: str, shift: int) -> str:
    return f"(({value} << {shift}) | ({value} >> {64 - shift}))"


def _kernel_source(family: str, lanes: int, rounds: int) -> str:
    lines = [
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "uint64_t quality_kernel(const uint64_t *left, const uint64_t *right,",
        "                       size_t n, uint64_t salt) {",
    ]
    for lane in range(lanes):
        lines.append(
            f"    uint64_t {_state_name(lane)} = {_SEED} + "
            f"((uint64_t){lane + 1} * {_LANE_SEED});"
        )
    lines.extend(
        [
            "    for (size_t index = 0; index < n; ++index) {",
            "        const uint64_t input_word = left[index] +",
            "            (right[index] ^ ((uint64_t)index * "
            + _INDEX_MIX
            + ")) ^ salt;",
            f"        for (unsigned round = 0; round < {rounds}u; ++round) {{",
            "            const uint64_t word = input_word +",
            "                ((uint64_t)round * " + _ROUND_MIX + ");",
        ]
    )

    if family == "polynomial":
        for lane in range(lanes):
            name = _state_name(lane)
            lines.append(
                f"            {name} = {name} * {_POLY_MULTIPLIER} + word + "
                f"((uint64_t){lane + 1} * {_LANE_SEED});"
            )
    elif family == "rotate_xor":
        for lane in range(lanes):
            name = _state_name(lane)
            lines.append(
                f"            {name} = ({_rotate(name, _lane_shift(lane))} ^ "
                f"(word + ((uint64_t){lane + 1} * {_ROUND_MIX}))) * {_ROTATE_MULTIPLIER};"
            )
    elif family == "cross_lane":
        # Left-to-right updates form a cyclic recurrence: each lane consumes
        # its predecessor's new value; lane zero consumes the old final lane.
        for lane in range(lanes):
            name = _state_name(lane)
            previous = _state_name(lane - 1 if lane else lanes - 1)
            lines.append(
                f"            {name} += ({_rotate(previous, _lane_shift(lane))} ^ word ^ "
                f"((uint64_t){lane + 1} * {_ROUND_MIX})) * {_CROSS_MULTIPLIER};"
            )
    elif family == "dot_product":
        for lane in range(lanes):
            name = _state_name(lane)
            lines.append(
                f"            {name} += (left[index] * right[index] + word + "
                f"((uint64_t){lane + 1} * {_LANE_SEED})) * (uint64_t){lane + 1};"
            )
    elif family == "masked_recurrence":
        for lane in range(lanes):
            name = _state_name(lane)
            shift = _lane_shift(lane)
            lines.extend(
                [
                    f"            if (((word >> {shift}) & UINT64_C(1)) != 0) {{",
                    f"                {name} = {name} * {_POLY_MULTIPLIER} + word +",
                    f"                    ((uint64_t){lane + 1} * {_LANE_SEED});",
                    "            } else {",
                    f"                {name} = ({name} ^ word) +",
                    f"                    ((uint64_t){lane + 1} * {_ROUND_MIX});",
                    "            }",
                ]
            )
    else:  # alternating_inputs
        lines.extend(
            [
                "            const uint64_t first = (round & 1u) ? right[index] : left[index];",
                "            const uint64_t second = (round & 1u) ? left[index] : right[index];",
            ]
        )
        for lane in range(lanes):
            name = _state_name(lane)
            previous = _state_name(lane - 1 if lane else lanes - 1)
            lines.append(
                f"            {name} = ({_rotate(f'({previous} ^ first)', _lane_shift(lane))} + "
                f"((second ^ word) * (uint64_t){lane + 1})) ^ ({name} * {_ALT_MULTIPLIER});"
            )

    lines.extend(["        }", "    }", "    uint64_t result = 0;"])
    for lane in range(lanes):
        lines.append(
            f"    result ^= {_state_name(lane)} + ((uint64_t){lane} * {_FOLD});"
        )
    lines.extend(["    return result;", "}", ""])
    return "\n".join(lines)


def _driver_source(family: str, lanes: int, rounds: int) -> str:
    lines = [
        "#define _POSIX_C_SOURCE 200809L",
        "#include <errno.h>",
        "#include <inttypes.h>",
        "#include <stdint.h>",
        "#include <stdio.h>",
        "#include <stdlib.h>",
        "#include <time.h>",
        "",
        f"#define LANE_COUNT {lanes}u",
        f"#define ROUND_COUNT {rounds}u",
        "#define VECTOR_CAPACITY 64u",
        "",
        "uint64_t quality_kernel(const uint64_t *left, const uint64_t *right,",
        "                       size_t n, uint64_t salt);",
        "",
        "static uint64_t next_random(uint64_t *state) {",
        "    uint64_t value = (*state += UINT64_C(0x9e3779b97f4a7c15));",
        "    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);",
        "    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);",
        "    return value ^ (value >> 31);",
        "}",
        "",
        "static void fill_inputs(uint64_t *left, uint64_t *right, size_t n, uint64_t seed) {",
        "    static const uint64_t edges[] = {",
        "        0, 1, 2, UINT64_MAX, UINT64_MAX - 1, UINT64_C(1) << 63,",
        "        (UINT64_C(1) << 63) - 1, UINT64_C(0xaaaaaaaaaaaaaaaa),",
        "    };",
        "    const size_t edge_count = sizeof(edges) / sizeof(edges[0]);",
        "    uint64_t random_state = seed;",
        "    for (size_t i = 0; i < n; ++i) {",
        "        if (i < edge_count) {",
        "            left[i] = edges[(i + (size_t)(seed & 7u)) & 7u];",
        "            right[i] = edges[(i + 3u + (size_t)(seed & 7u)) & 7u];",
        "        } else {",
        "            left[i] = next_random(&random_state);",
        "            right[i] = next_random(&random_state);",
        "        }",
        "    }",
        "}",
    ]
    if family in {"rotate_xor", "cross_lane", "alternating_inputs"}:
        lines.extend(
            [
                "",
                "static uint64_t rotate_left(uint64_t value, unsigned shift) {",
                "    return (value << shift) | (value >> (64u - shift));",
                "}",
            ]
        )
    lines.extend(
        [
            "",
            "static uint64_t reference_kernel(const uint64_t *left, const uint64_t *right,",
            "                                 size_t n, uint64_t salt) {",
            "    uint64_t state[LANE_COUNT];",
            "    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
            f"        state[lane] = {_SEED} + ((uint64_t)(lane + 1) * {_LANE_SEED});",
            "    }",
            "    for (size_t index = 0; index < n; ++index) {",
            "        const uint64_t input_word = left[index] +",
            "            (right[index] ^ ((uint64_t)index * "
            + _INDEX_MIX
            + ")) ^ salt;",
            "        for (unsigned round = 0; round < ROUND_COUNT; ++round) {",
            "            const uint64_t word = input_word +",
            "                ((uint64_t)round * " + _ROUND_MIX + ");",
        ]
    )
    if family == "polynomial":
        lines.extend(
            [
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                f"                state[lane] = state[lane] * {_POLY_MULTIPLIER} + word +",
                f"                    ((uint64_t)(lane + 1) * {_LANE_SEED});",
                "            }",
            ]
        )
    elif family == "rotate_xor":
        lines.extend(
            [
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                "                const unsigned shift = (unsigned)(lane % 63u) + 1u;",
                "                state[lane] = (rotate_left(state[lane], shift) ^ (word +",
                f"                    ((uint64_t)(lane + 1) * {_ROUND_MIX}))) * {_ROTATE_MULTIPLIER};",
                "            }",
            ]
        )
    elif family == "cross_lane":
        lines.extend(
            [
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                "                const size_t previous = lane == 0 ? LANE_COUNT - 1 : lane - 1;",
                "                const unsigned shift = (unsigned)(lane % 63u) + 1u;",
                "                state[lane] += (rotate_left(state[previous], shift) ^ word ^",
                f"                    ((uint64_t)(lane + 1) * {_ROUND_MIX})) * {_CROSS_MULTIPLIER};",
                "            }",
            ]
        )
    elif family == "dot_product":
        lines.extend(
            [
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                "                state[lane] += (left[index] * right[index] + word +",
                f"                    ((uint64_t)(lane + 1) * {_LANE_SEED})) * (uint64_t)(lane + 1);",
                "            }",
            ]
        )
    elif family == "masked_recurrence":
        lines.extend(
            [
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                "                const unsigned shift = (unsigned)(lane % 63u) + 1u;",
                "                if (((word >> shift) & UINT64_C(1)) != 0) {",
                f"                    state[lane] = state[lane] * {_POLY_MULTIPLIER} + word +",
                f"                        ((uint64_t)(lane + 1) * {_LANE_SEED});",
                "                } else {",
                f"                    state[lane] = (state[lane] ^ word) + ((uint64_t)(lane + 1) * {_ROUND_MIX});",
                "                }",
                "            }",
            ]
        )
    else:  # alternating_inputs
        lines.extend(
            [
                "            const uint64_t first = (round & 1u) ? right[index] : left[index];",
                "            const uint64_t second = (round & 1u) ? left[index] : right[index];",
                "            for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
                "                const size_t previous = lane == 0 ? LANE_COUNT - 1 : lane - 1;",
                "                const unsigned shift = (unsigned)(lane % 63u) + 1u;",
                "                state[lane] = (rotate_left(state[previous] ^ first, shift) +",
                f"                    ((second ^ word) * (uint64_t)(lane + 1))) ^ (state[lane] * {_ALT_MULTIPLIER});",
                "            }",
            ]
        )
    lines.extend(
        [
            "        }",
            "    }",
            "    uint64_t result = 0;",
            "    for (size_t lane = 0; lane < LANE_COUNT; ++lane) {",
            f"        result ^= state[lane] + ((uint64_t)lane * {_FOLD});",
            "    }",
            "    return result;",
            "}",
            "",
            "static int check_case(size_t n, uint64_t seed, uint64_t salt) {",
            "    uint64_t left[VECTOR_CAPACITY];",
            "    uint64_t right[VECTOR_CAPACITY];",
            "    fill_inputs(left, right, n, seed);",
            "    return quality_kernel(left, right, n, salt) ==",
            "        reference_kernel(left, right, n, salt);",
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
            "    if (argc == 1) {",
            "        *iterations = 1000;",
            "        return 1;",
            "    }",
            "    if (argc != 2 || argv[1][0] == '\\0' || argv[1][0] == '-') {",
            "        return 0;",
            "    }",
            "    errno = 0;",
            "    char *end = NULL;",
            "    unsigned long long parsed = strtoull(argv[1], &end, 10);",
            "    if (errno != 0 || end == argv[1] || *end != '\\0' || parsed == 0 ||",
            "        parsed > UINT64_MAX) {",
            "        return 0;",
            "    }",
            "    *iterations = (uint64_t)parsed;",
            "    return 1;",
            "}",
            "",
            "int main(int argc, char **argv) {",
            "    uint64_t iterations;",
            "    if (!parse_iterations(argc, argv, &iterations)) {",
            '        fprintf(stderr, "usage: %s [positive-iterations]\\n", argv[0]);',
            "        return 2;",
            "    }",
            "    static const size_t lengths[] = {0, 1, 2, 7, 17, 39, VECTOR_CAPACITY};",
            "    for (size_t i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i) {",
            "        if (!check_case(lengths[i], UINT64_C(0x123456789abcdef0) + (uint64_t)i,",
            "                        UINT64_C(0xfedcba9876543210) ^ (uint64_t)i)) {",
            '            fprintf(stderr, "kernel mismatch for n=%zu\\n", lengths[i]);',
            "            return 1;",
            "        }",
            "    }",
            "    uint64_t left[VECTOR_CAPACITY];",
            "    uint64_t right[VECTOR_CAPACITY];",
            "    fill_inputs(left, right, VECTOR_CAPACITY, UINT64_C(0xfeedfacecafebeef));",
            "    uint64_t checksum = UINT64_C(0x6a09e667f3bcc909);",
            "    struct timespec start;",
            "    struct timespec end;",
            "    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {",
            '        perror("clock_gettime");',
            "        return 1;",
            "    }",
            "    for (uint64_t iteration = 0; iteration < iterations; ++iteration) {",
            "        checksum = (checksum ^ quality_kernel(left, right, VECTOR_CAPACITY, iteration)) *",
            "            UINT64_C(0x9e3779b97f4a7c15) + iteration;",
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
