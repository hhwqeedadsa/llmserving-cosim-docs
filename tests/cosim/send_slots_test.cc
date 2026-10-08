// Run through the parent joint build's cosim-send-slots-test target.
#include "astra-sim/workload/HardwareResource.hh"
#include <iostream>
#include <stdexcept>

namespace {
void check(bool ok) {
    if (!ok) throw std::runtime_error("HardwareResource send-slot check failed");
}
template<class F> void rejects(F f) {
    try { f(); } catch (const std::exception&) { return; }
    throw std::runtime_error("Invalid send-slot operation was accepted");
}
auto node(uint64_t id, ChakraProtoMsg::NodeType type) {
    auto data = std::make_shared<ChakraProtoMsg::Node>();
    data->set_id(id); data->set_type(type);
    return std::make_shared<Chakra::ETFeederNode>(data);
}
}
int main() {
    try {
        using namespace ChakraProtoMsg;
        auto a = node(1, COMM_SEND_NODE), b = node(2, COMM_SEND_NODE), c = node(3, COMM_SEND_NODE);
        auto recv = node(4, COMM_RECV_NODE), collective = node(5, COMM_COLL_NODE);
        auto comp = node(6, COMP_NODE), memory = node(7, MEM_LOAD_NODE);
        AstraSim::HardwareResource legacy(1);
        legacy.occupy(a);
        check(!legacy.is_available(b) && !legacy.is_available(collective));
        check(legacy.is_available(recv) && legacy.is_available(comp) && legacy.is_available(memory));
        legacy.occupy(recv); legacy.release(recv); check(legacy.num_in_flight_gpu_comm_ops == 1);
        rejects([&] { legacy.configure_p2p_send_slots(2); });
        legacy.release(a); check(legacy.num_in_flight_gpu_comm_ops == 0);
        legacy.occupy(collective); check(!legacy.is_available(a)); legacy.release(collective);
        check(legacy.num_gpu_comms == 2 && legacy.active_p2p_sends() == 0);

        AstraSim::HardwareResource resource(1);
        check(resource.p2p_send_capacity() == 0);
        rejects([&] { resource.configure_p2p_send_slots(0); });
        resource.configure_p2p_send_slots(2);
        check(resource.p2p_send_capacity() == 2);
        resource.occupy(a); check(resource.is_available(b)); resource.occupy(b);
        check(resource.active_p2p_sends() == 2 && resource.num_in_flight_gpu_comm_ops == 2);
        check(!resource.is_available(c) && !resource.is_available(collective));
        rejects([&] { resource.occupy(c); }); rejects([&] { resource.occupy(a); });
        rejects([&] { resource.release(c); }); rejects([&] { resource.configure_p2p_send_slots(1); });
        check(resource.is_available(recv) && resource.is_available(comp));
        resource.occupy(recv); resource.release(recv); check(resource.active_p2p_sends() == 2);
        resource.release(b); // Out-of-order completion must free exactly this SEND.
        check(resource.is_available(c) && !resource.is_available(a)); resource.occupy(c);
        rejects([&] { resource.release(b); });
        resource.release(a); resource.release(c);
        check(resource.active_p2p_sends() == 0 && resource.num_in_flight_gpu_comm_ops == 0);
        resource.occupy(collective); check(!resource.is_available(a) && resource.is_available(recv));
        resource.release(collective);
        resource.configure_p2p_send_slots(1); resource.occupy(a);
        check(resource.p2p_send_capacity() == 1);
        check(!resource.is_available(b)); resource.release(a);
        check(resource.active_p2p_sends() == 0 && resource.num_in_flight_gpu_comm_ops == 0 && resource.num_gpu_comms == 5);
        std::cout << "PASS: default/collective resource rules, one/two P2P slots, RECV independence, identity release\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
