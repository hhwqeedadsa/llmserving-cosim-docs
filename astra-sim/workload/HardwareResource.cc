/******************************************************************************
This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
*******************************************************************************/

// TODO: HardwareResource.cc should be moved to the system layer.

#include "astra-sim/workload/HardwareResource.hh"
#include <stdexcept>

using namespace std;
using namespace AstraSim;
using namespace Chakra;

typedef ChakraProtoMsg::NodeType ChakraNodeType;

void HardwareResource::configure_p2p_send_slots(uint32_t slots) {
    if (slots == 0 || num_in_flight_gpu_comm_ops != 0 || !p2p_sends.empty()) {
        throw std::invalid_argument("P2P slots require a positive limit and idle communication resources");
    }
    p2p_send_slots = slots;
}

HardwareResource::HardwareResource(uint32_t num_npus)
    : num_npus(num_npus),
      num_in_flight_cpu_ops(0),
      num_in_flight_gpu_comm_ops(0),
      num_in_flight_gpu_comp_ops(0) {

    num_cpu_ops = 0;
    num_gpu_ops = 0;
    num_gpu_comms = 0;

    tics_cpu_ops = 0;
    tics_gpu_ops = 0;
    tics_gpu_comms = 0;

    cpu_ops_node = NULL;
    gpu_ops_node = NULL;
    gpu_comms_node = NULL;

}

void HardwareResource::occupy(const shared_ptr<Chakra::ETFeederNode> node) {
    if (p2p_send_slots && !node->is_cpu_op() && node->type() == ChakraNodeType::COMM_SEND_NODE) {
        if (!is_available(node) || !p2p_sends.insert(node->id()).second) {
            throw std::logic_error("P2P SEND has no free slot or is already in flight");
        }
        ++num_in_flight_gpu_comm_ops;
        ++num_gpu_comms;
        return;
    }
    if (node->is_cpu_op()) {
        assert(num_in_flight_cpu_ops == 0);
        ++num_in_flight_cpu_ops;
        ++num_cpu_ops;
    } else {
        if (node->type() == ChakraNodeType::COMP_NODE) {
            assert(num_in_flight_gpu_comp_ops == 0);
            ++num_in_flight_gpu_comp_ops;
            ++num_gpu_ops;
            gpu_ops_node = node;
        } else {
            if (node->type() == ChakraNodeType::COMM_RECV_NODE){
                return;
            }
            else if (node->type() == ChakraNodeType::MEM_LOAD_NODE ||
                     node->type() == ChakraNodeType::MEM_STORE_NODE ||
                     node->type() == ChakraNodeType::PIM_COMP_NODE) {
                return;
            }
            assert(num_in_flight_gpu_comm_ops == 0);
            ++num_in_flight_gpu_comm_ops;
            ++num_gpu_comms;
            gpu_comms_node = node;
        }
    }
}

void HardwareResource::release(const shared_ptr<Chakra::ETFeederNode> node) {
    if (p2p_send_slots && !node->is_cpu_op() && node->type() == ChakraNodeType::COMM_SEND_NODE) {
        if (p2p_sends.erase(node->id()) != 1 || num_in_flight_gpu_comm_ops == 0) {
            throw std::logic_error("P2P SEND release does not match an occupied slot");
        }
        --num_in_flight_gpu_comm_ops;
        return;
    }
    if (node->is_cpu_op()) {
        --num_in_flight_cpu_ops;
        assert(num_in_flight_cpu_ops == 0);
    } else {
        if (node->type() == ChakraNodeType::COMP_NODE) {
            --num_in_flight_gpu_comp_ops;
            assert(num_in_flight_gpu_comp_ops == 0);
        } else {
            if (node->type() == ChakraNodeType::COMM_RECV_NODE){
                return;
            } else if (node->type() == ChakraNodeType::MEM_LOAD_NODE ||
                       node->type() == ChakraNodeType::MEM_STORE_NODE ||
                       node->type() == ChakraNodeType::PIM_COMP_NODE) {
                return;
            }
            --num_in_flight_gpu_comm_ops;
            assert(num_in_flight_gpu_comm_ops == 0);
        }
    }
}

bool HardwareResource::is_available(
    const shared_ptr<Chakra::ETFeederNode> node) const {
    if (p2p_send_slots && !node->is_cpu_op() && node->type() == ChakraNodeType::COMM_SEND_NODE) {
        // An original collective still exclusively owns the communication
        // resource. RECV continues through the original slot-free path.
        return num_in_flight_gpu_comm_ops == p2p_sends.size() &&
               p2p_sends.size() < p2p_send_slots && p2p_sends.count(node->id()) == 0;
    }
    if (node->is_cpu_op()) {
        if (num_in_flight_cpu_ops == 0) {
            return true;
        } else {
            return false;
        }
    } else {
        if (node->type() == ChakraNodeType::COMP_NODE) {
            if (num_in_flight_gpu_comp_ops == 0) {
                return true;
            } else {
                return false;
            }
        } else {
            if (num_in_flight_gpu_comm_ops == 0) {
                return true;
            } else {
            if (node->type() == ChakraNodeType::COMM_RECV_NODE ||
                node->type() == ChakraNodeType::MEM_LOAD_NODE ||
                node->type() == ChakraNodeType::MEM_STORE_NODE ||
                node->type() == ChakraNodeType::PIM_COMP_NODE) {
                // concurerent memory access is handled in Memory Backend
                return true;
            }
                if (num_in_flight_gpu_comm_ops == 0) {
                    return true;
                }
                return false;
            }
        }
    }
}

void HardwareResource::report() {
    cout << "num_cpu_ops: " << num_cpu_ops << endl;
    cout << "num_gpu_ops: " << num_gpu_ops << endl;
    cout << "num_gpu_comms: " << num_gpu_comms << endl;

    cout << "tics_cpu_ops: " << tics_cpu_ops << endl;
    cout << "tics_gpu_ops: " << tics_gpu_ops << endl;
    cout << "tics_gpu_comms: " << tics_gpu_comms << endl;
}
