// SPDX-License-Identifier: MIT
// Generated discovery candidate. Resource fit has not been established.
#include <core.p4>
#if __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

header ethernet_t { bit<48> dst; bit<48> src; bit<16> ether_type; }
header fit_t {
    bit<32> flow;
    bit<64> tenant;
    bit<32> token;
    bit<32> accumulator;
    bit<32> result0;
    bit<32> result1;
    bit<32> result2;
    bit<32> result3;
    bit<32> result4;
    bit<32> result5;
    bit<32> result6;
    bit<32> result7;
    bit<16> reserved;
}
struct headers_t { ethernet_t ethernet; fit_t fit; }
struct metadata_t { }

parser IngressParser(packet_in pkt, out headers_t hdr, out metadata_t md,
                     out ingress_intrinsic_metadata_t ig_intr_md) {
    state start {
        pkt.extract(ig_intr_md);
        transition select(ig_intr_md.resubmit_flag) { 0: port_metadata; default: reject; }
    }
    state port_metadata { pkt.advance(PORT_METADATA_SIZE); transition ethernet; }
    state ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) { 16w0x88b5: fit; default: accept; }
    }
    state fit { pkt.extract(hdr.fit); transition accept; }
}

control Ingress(inout headers_t hdr, inout metadata_t md,
                in ingress_intrinsic_metadata_t ig_intr_md,
                in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
                inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
                inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {
    action set_chain_0(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_0 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_0; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_1(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_1 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_1; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_2(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_2 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_2; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_3(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_3 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_3; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_4(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_4 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_4; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_5(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_5 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_5; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_6(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_6 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_6; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_7(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_7 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_7; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_8(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_8 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_8; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_9(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_9 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_9; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_10(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_10 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_10; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_chain_11(bit<32> value, bit<32> delta) { hdr.fit.token = value; hdr.fit.accumulator = hdr.fit.accumulator + delta; }
                                                                                
                                                                                
    @pack(5)                                                                    
                                                                                
    table chain_11 {
        key = { hdr.fit.token : exact; }
        actions = { set_chain_11; NoAction; }
        const default_action = NoAction();
        size = 32768;
    }
    action set_side_0(bit<32> value) { hdr.fit.result0 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_0 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_0; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_1(bit<32> value) { hdr.fit.result1 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_1 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_1; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_2(bit<32> value) { hdr.fit.result2 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_2 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_2; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_3(bit<32> value) { hdr.fit.result3 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_3 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_3; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_4(bit<32> value) { hdr.fit.result4 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_4 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_4; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_5(bit<32> value) { hdr.fit.result5 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_5 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_5; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_6(bit<32> value) { hdr.fit.result6 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_6 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_6; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }
    action set_side_7(bit<32> value) { hdr.fit.result7 = value; }
                                                                                
                                                                                
                                                                                
                                                                                
    table side_7 {
        key = { hdr.fit.flow : exact; hdr.fit.tenant : exact; }
        actions = { set_side_7; NoAction; }
        const default_action = NoAction();
        size = 524288;
    }

    apply {
        ig_tm_md.ucast_egress_port = ig_intr_md.ingress_port;
        if (hdr.fit.isValid()) {
            side_0.apply();
            side_1.apply();
            side_2.apply();
            side_3.apply();
            side_4.apply();
            side_5.apply();
            side_6.apply();
            side_7.apply();
            chain_0.apply();
            chain_1.apply();
            chain_2.apply();
            chain_3.apply();
            chain_4.apply();
            chain_5.apply();
            chain_6.apply();
            chain_7.apply();
            chain_8.apply();
            chain_9.apply();
            chain_10.apply();
            chain_11.apply();
        }
    }
}

control IngressDeparser(packet_out pkt, inout headers_t hdr, in metadata_t md,
                        in ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md) {
    apply { pkt.emit(hdr); }
}

parser EgressParser(packet_in pkt, out headers_t hdr, out metadata_t md,
                    out egress_intrinsic_metadata_t eg_intr_md) {
    state start { pkt.extract(eg_intr_md); transition ethernet; }
    state ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) { 16w0x88b5: fit; default: accept; }
    }
    state fit { pkt.extract(hdr.fit); transition accept; }
}
control Egress(inout headers_t hdr, inout metadata_t md,
               in egress_intrinsic_metadata_t eg_intr_md,
               in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
               inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
               inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {
    apply { }
}
control EgressDeparser(packet_out pkt, inout headers_t hdr, in metadata_t md,
                       in egress_intrinsic_metadata_for_deparser_t eg_dprsr_md) {
    apply { pkt.emit(hdr); }
}
Pipeline(IngressParser(), Ingress(), IngressDeparser(),
         EgressParser(), Egress(), EgressDeparser()) pipe;
Switch(pipe) main;
