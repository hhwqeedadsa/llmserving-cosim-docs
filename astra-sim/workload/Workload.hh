/******************************************************************************
This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
*******************************************************************************/

#ifndef __WORKLOAD_HH__
#define __WORKLOAD_HH__

#include <memory>
#include <functional>
#include <string>
#include <unordered_map>
#include <queue>
#include <unordered_set>

#include "astra-sim/system/Callable.hh"
#include "astra-sim/system/CommunicatorGroup.hh"
#include "astra-sim/workload/HardwareResource.hh"
#include "extern/graph_frontend/chakra/src/feeder/et_feeder.h"

namespace AstraSim {

class Sys;
class DataSet;

class Workload : public Callable {
  public:
    Workload(Sys* sys,
             std::string et_filename,
             std::string comm_group_filename);
    ~Workload();

    // communicator groups
    void initialize_comm_group(std::string comm_group_filename);

    // event-based simulation
    void issue_dep_free_nodes();
    void issue(std::shared_ptr<Chakra::ETFeederNode> node);
    void issue_replay(std::shared_ptr<Chakra::ETFeederNode> node);
    // void issue_remote_mem(std::shared_ptr<Chakra::ETFeederNode> node); integrated into issue_mem
    void issue_mem(std::shared_ptr<Chakra::ETFeederNode> node);
    void issue_comp(std::shared_ptr<Chakra::ETFeederNode> node);
    void issue_comm(std::shared_ptr<Chakra::ETFeederNode> node);
    void skip_invalid(std::shared_ptr<Chakra::ETFeederNode> node);
    void call(EventType event, CallData* data);
    void fire();
    void add_workload(const std::string& new_filename, const std::vector<Sys*>& systems);
    void sleep_workload(const std::vector<Sys*>& systems);

    // Opt-in joint execution: callers prepare every rank before installing any.
    void bind_graph(const std::string& graph_id);
    bool can_load_graph() const;
    bool has_graph_id(const std::string& id) const { return used_graph_ids_.count(id) != 0; }
    void load_graph(std::unique_ptr<Chakra::ETFeeder> feeder, const std::string& graph_id);
    void activate_graph();
    const std::string& graph_id() const { return graph_id_; }
    bool graph_active() const { return graph_active_; }
    uint64_t stale_callbacks() const { return stale_callbacks_; }

    // Joint-mode passive snapshots after a complete dependency-free dispatch
    // scan. The observer must not schedule work or mutate execution state.
    using SendStateObserver = std::function<void(uint64_t, uint32_t, uint32_t)>;
    void set_send_state_observer(SendStateObserver observer);
    void notify_send_state() const;

    // stats
    void report();

    Chakra::ETFeeder* et_feeder;
    CommunicatorGroup* comm_group;
    HardwareResource* hw_resource;
    Sys* sys;
    std::unordered_map<int, uint64_t> collective_comm_node_id_map;
    std::unordered_map<int, DataSet*> collective_comm_wrapper_map;
    bool is_finished;
    uint32_t iteration;
    std::string filename;

    bool is_sleep;
    std::queue<std::string> pending_workloads;

  private:
    std::string graph_id_;
    std::unordered_set<std::string> used_graph_ids_;
    std::unordered_set<uint64_t> active_nodes_;
    bool graph_active_ = false;
    uint64_t stale_callbacks_ = 0;
    SendStateObserver send_state_observer_;
    uint64_t ready_send_count_ = 0;
    std::string graph_log_suffix() const;

};

}  // namespace AstraSim

#endif /* __WORKLOAD_HH__ */
