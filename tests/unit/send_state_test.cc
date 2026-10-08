#include "send_state.hh"
#include <filesystem>
#include <iostream>

using Json=nlohmann::json;
void Check(bool value) { if(!value) throw std::runtime_error("send-state assertion failed"); }
template<class F> void Reject(F call) {
    bool failed=false;try { call(); } catch(const std::exception&) { failed=true; } Check(failed);
}
int main(int argc,char** argv) {
    Check(argc==2);std::filesystem::path root=std::filesystem::absolute(argv[1]);
    Check(!std::filesystem::exists(root));std::filesystem::create_directories(root);
    std::filesystem::current_path(root);
    auto policy=Json::parse(R"({"schema_version":"moe-ub-send-state-config/1","max_records_per_rank":8,"max_total_records":16})");
    auto storage=Json::parse(R"({"schema_version":"moe-ub-record-storage/1","max_compressed_bytes":1000000,"max_buffer_bytes":131072,"max_record_bytes":65536})");
    Json report=Json::object();moe_ub::ResultRecords records(report,storage,false,true);
    moe_ub::SendStateRecords states(records,policy,{2,7},100);
    states.Observe(2,0,0,0,0);states.Observe(7,0,0,0,0);
    states.Observe(2,0,0,0,2);states.Observe(2,10,3,2,2);
    states.Observe(2,10,2,2,2);states.Observe(2,11,2,2,2); // deduplicated
    Reject([&]{states.Observe(2,9,0,0,2);});
    Reject([&]{states.Observe(2,20,0,3,2);});
    Reject([&]{states.Finish();}); // rank 2 has not drained
    states.Observe(2,20,0,0,2);
    auto capture=states.Finish();Check(capture.at("records")==8);
    Check(capture.at("ranks").at(0).at("records")==6);
    Reject([&]{states.Observe(2,21,0,0,2);});Reject([&]{states.Finish();});
    records.Finish();auto file=records.Store()->Seal("send_state.jsonl.gz");
    std::vector<Json> rows;
    moe_ub::ReadRecords("records",file,[&](const auto& line){rows.push_back(Json::parse(line));});
    Check(rows.size()==8 && rows[3].at("time_ns")==10 && rows[4].at("time_ns")==10);
    Check(rows[3].at("ready_send_count")==3 && rows[4].at("ready_send_count")==2);
    Check(rows[6].at("event_kind")=="final" && rows[7].at("time_ns")==100);
    std::filesystem::create_directory(root/"limited");std::filesystem::current_path(root/"limited");
    policy["max_records_per_rank"]=2u;policy["max_total_records"]=2u;
    Reject([&]{moe_ub::SendStateRecords::ValidatePolicy(policy,2);});
    Json limited_report=Json::object();moe_ub::ResultRecords limited(limited_report,storage,false,true);
    moe_ub::SendStateRecords bounded(limited,policy,{2},100);
    bounded.Observe(2,0,0,0,0);bounded.Observe(2,0,0,0,1);
    Reject([&]{bounded.Observe(2,1,1,1,1);});Reject([&]{bounded.Finish();});
    Check(limited.Count("send_state")==2);
    policy["max_total_records"]=true;Reject([&]{moe_ub::SendStateRecords::ValidatePolicy(policy,1);});
    std::cout<<"PASS send-state ordering, deduplication, drain, final boundary and record limits\n";
}
