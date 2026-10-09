/******************************************************************************
This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
*******************************************************************************/

// TODO: HardwareResource.hh should be moved to the system layer.

#ifndef __HARDWARE_RESOURCE_HH__
#define __HARDWARE_RESOURCE_HH__

#include <cstdint>
#include <unordered_set>
#include <unordered_map>
#include <string>

#include "extern/graph_frontend/chakra/src/feeder/et_feeder.h"

namespace AstraSim {

class HardwareResource {
  public:
    HardwareResource(uint32_t num_npus);
    void occupy(const std::shared_ptr<Chakra::ETFeederNode> node);
    void release(const std::shared_ptr<Chakra::ETFeederNode> node);
    bool is_available(const std::shared_ptr<Chakra::ETFeederNode> node) const;
    void report();
    // Opt-in for explicit point-to-point graphs. Default collective/native
    // scheduling retains the original single communication resource.
    void configure_p2p_send_slots(uint32_t slots);
    // Managed graphs may own local buffers in their DAG and leave protocol
    // capacity to the network backend. This never changes legacy scheduling.
    void configure_p2p_protocol_resources();
    void configure_compute_resources(const std::unordered_map<uint64_t, std::string>& nodes);
    uint32_t active_p2p_sends() const { return p2p_sends.size(); }
    uint32_t p2p_send_capacity() const { return p2p_send_slots; }

    std::shared_ptr<Chakra::ETFeederNode> cpu_ops_node;
    std::shared_ptr<Chakra::ETFeederNode> gpu_ops_node;
    std::shared_ptr<Chakra::ETFeederNode> gpu_comms_node;

    const uint32_t num_npus;
    uint32_t num_in_flight_cpu_ops;
    uint32_t num_in_flight_gpu_comp_ops;
    uint32_t num_in_flight_gpu_comm_ops;

    uint64_t num_cpu_ops;
    uint64_t num_gpu_ops;
    uint64_t num_gpu_comms;

    uint64_t tics_cpu_ops;
    uint64_t tics_gpu_ops;
    uint64_t tics_gpu_comms;

  private:
    uint32_t p2p_send_slots = 0;
    std::unordered_set<uint64_t> p2p_sends;
    bool protocol_owned_p2p = false;
    bool named_compute = false;
    std::unordered_map<uint64_t, std::string> compute_resources;
    std::unordered_set<std::string> occupied_compute;
    std::unordered_set<uint64_t> active_compute;
};

}  // namespace AstraSim

#endif /* __HARDWARE_RESOURCE_HH__ */
