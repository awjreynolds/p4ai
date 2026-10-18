"""Synthetic contract and mechanism checks; no measured workload fixtures."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.workload_heuristic import ACTION_ORDER, choose  # noqa: E402


def packet():
    return {
        "actions": [{"id": index} for index in range(6)],
        "features": {
            "element_bits": [32] * 6,
            "inner_a_stride_bytes": [64] * 6,
            "inner_b_stride_bytes": [64] * 6,
            "inner_c_stride_bytes": [64] * 6,
            "ir_max_vector_bits": [0] * 6,
            "target_vector_register_bits": [128] * 6,
            "middle_c_stride_bytes": [64] * 6,
            "ir_instruction_count": [100] * 6,
        },
    }


class WorkloadHeuristicTests(unittest.TestCase):
    def test_scalar_fallback_and_no_packet_mutation(self):
        data = packet()
        data["features"]["ir_instruction_count"][5] = 1
        before = copy.deepcopy(data)
        self.assertEqual(choose(data), "ijk")
        self.assertEqual(data, before)

    def test_contiguous_output_prefers_reduction_in_middle(self):
        data = packet()
        f = data["features"]
        for index in (1, 4):
            f["inner_a_stride_bytes"][index] = 0
            f["inner_b_stride_bytes"][index] = 4
            f["inner_c_stride_bytes"][index] = 4
            f["ir_max_vector_bits"][index] = 128
        f["middle_c_stride_bytes"][1] = 0
        f["ir_instruction_count"][4] = 1
        f["inner_a_stride_bytes"][0] = 4
        f["inner_b_stride_bytes"][0] = 4
        f["inner_c_stride_bytes"][0] = 0
        f["ir_max_vector_bits"][0] = 512
        self.assertEqual(choose(data), "ikj")

    def test_contiguous_reduction_beats_strided_vectors(self):
        data = packet()
        f = data["features"]
        f["ir_max_vector_bits"] = [128] * 6
        for index in (0, 2):
            f["inner_a_stride_bytes"][index] = 4
            f["inner_b_stride_bytes"][index] = 4
            f["inner_c_stride_bytes"][index] = 0
        f["ir_instruction_count"][4] = 1
        self.assertEqual(choose(data), "ijk")

    def test_expanded_reduction_prefers_smaller_ir(self):
        data = packet()
        f = data["features"]
        f["ir_max_vector_bits"] = [128] * 6
        for index in (0, 2):
            f["inner_c_stride_bytes"][index] = 0
            f["ir_max_vector_bits"][index] = 512
        f["ir_instruction_count"][2] = 50
        f["ir_instruction_count"][4] = 1
        self.assertEqual(choose(data), "jik")

    def test_other_vector_code_prefers_smaller_ir(self):
        data = packet()
        f = data["features"]
        f["ir_max_vector_bits"][1] = 128
        f["ir_max_vector_bits"][4] = 128
        f["ir_instruction_count"][4] = 50
        self.assertEqual(choose(data), "kij")

    def test_canonical_arrays_ignore_action_listing_order_and_labels(self):
        data = packet()
        data["actions"].reverse()
        data["loss"] = [0, 0, 0, 0, 0, 999]
        data["features"]["ir_max_vector_bits"][5] = 128
        self.assertEqual(choose(data), ACTION_ORDER[5])


if __name__ == "__main__":
    unittest.main()
