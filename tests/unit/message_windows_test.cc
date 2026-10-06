#include "message_windows.hh"
#include <iostream>
#include <tuple>
#include <vector>

namespace {void Check(bool value,const char* why) {if(!value) throw std::runtime_error(why);}}
int main() {
    using namespace moe_ub;
    try {
        using Row=std::tuple<uint64_t,ByteCounts,MessageAttribution>;
        std::vector<Row> rows;
        MessageWindows windows({5,26,10,{0,0}},
            {MessageCategory::Business,MessageCategory::AttachedMetadata,MessageCategory::DispatchCount,MessageCategory::CombineFlag},10,
            [&](auto i,const auto& row,const auto& truth){rows.emplace_back(i,row,truth);});
        windows.Add(4,99,{10,4,0,0,0},0,"outside");
        windows.Add(5,30,{10,4,0,0,0},2,"vector");
        windows.Add(14,64,{0,0,32,0,0},0,"zero-business-status");
        windows.Add(15,20,{},0,"");windows.Add(25,48,{0,0,0,32,0},0,"done");windows.Finish();
        Check(rows.size()==3,"missing packet aggregate tail");
        const auto& [i,row,truth]=rows[0];
        Check(i==0 && row.packets==2 && row.whole==94 && row.business==10 && row.metadata==36 && row.padding==2 && row.overhead==46,"independent aggregate bytes");
        Check(truth.at({"zero-business-status",MessageCategory::DispatchCount})==32,"status lacks direct operation attribution");
        Check(std::get<1>(rows[1]).control==20 && std::get<2>(rows[1]).empty(),"protocol control falsely attributed as application metadata");
        Check(windows.Packets()==4 && windows.Bytes()==162 && windows.PeakEntries()==3,"aggregate audit totals");
        std::vector<std::tuple<std::string,uint64_t,uint64_t,uint64_t>> states;
        DetailQueueState detail(10,20,7,0,[&](auto k,auto t,auto v,auto f){states.emplace_back(k,t,v,f);});
        detail.Update(5,11,{});detail.Update(10,0,{});detail.Update(15,{},3);detail.Finish(25);
        Check(states==decltype(states)({{"initial",10,11,0},{"update",10,0,0},{"update",15,0,3},{"final",20,0,3}}),"lazy detail boundary snapshots");
        bool rejected=false;
        try {MessageWindows small({0,10,10,{0,0}},{MessageCategory::Business,MessageCategory::AttachedMetadata},1,[](auto,const auto&,const auto&){});
            small.Add(0,20,{10,1,0,0,0},0,"two-categories");}
        catch(const std::runtime_error&) {rejected=true;}
        Check(rejected,"attribution capacity silently exceeded");
        std::cout<<"PASS direct classified aggregates, status attribution, ACK exclusion, detail boundary state and finite entries\n";return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';return 1;}
}
