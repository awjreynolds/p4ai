"""Four source/compiler predicates for the six canonical loop orders."""

ACTION_ORDER = ("ijk", "ikj", "jik", "jki", "kij", "kji")


def choose(packet: dict) -> str:
    """Choose from feature arrays indexed by ACTION_ORDER, without runtime labels."""
    features = packet["features"]

    def priority(index: int) -> tuple:
        element_bytes = features["element_bits"][index] / 8
        a_stride = features["inner_a_stride_bytes"][index]
        b_stride = features["inner_b_stride_bytes"][index]
        c_stride = features["inner_c_stride_bytes"][index]
        vector_bits = features["ir_max_vector_bits"][index]

        # Each predicate denotes a mechanism, rather than a workload identity.
        vectorized = vector_bits > 0
        contiguous_output = (
            vectorized
            and c_stride == element_bytes
            and a_stride + b_stride == element_bytes
        )
        contiguous_reduction = (
            vectorized and a_stride == element_bytes and b_stride == element_bytes
        )
        expanded_reduction = (
            c_stride == 0
            and vector_bits > features["target_vector_register_bits"][index]
        )

        # Keep an output slice hot across k; otherwise prefer smaller emitted IR.
        return (
            -contiguous_output,
            -contiguous_reduction,
            -expanded_reduction,
            -vectorized,
            contiguous_output * features["middle_c_stride_bytes"][index],
            vectorized * features["ir_instruction_count"][index],
            index,
        )

    return ACTION_ORDER[min(range(len(ACTION_ORDER)), key=priority)]
