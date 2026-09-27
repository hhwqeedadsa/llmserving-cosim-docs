#include "astra-sim/workload/HardwareResource.hh"
#include "et_def.pb.h"
#include "ns3/simulator.h"
#include "ns3/ub-app.h"
#include "protoio.hh"

#include <filesystem>
#include <iostream>
#include <link.h>
#include <stdexcept>

namespace
{
int PrintLoadedLibrary(dl_phdr_info* info, std::size_t, void*)
{
    if (info->dlpi_name && info->dlpi_name[0])
    {
        std::cout << "library=" << info->dlpi_name << '\n';
    }
    return 0;
}
}

int main(int argc, char** argv)
{
    try
    {
        if (argc != 2 || std::filesystem::exists(argv[1]))
        {
            throw std::runtime_error("Supply a new output path for the probe graph");
        }
        {
            ProtoOutputStream output(argv[1]);
            ChakraProtoMsg::GlobalMetadata metadata;
            metadata.set_version("m0-link-probe");
            output.write(metadata);
            ChakraProtoMsg::Node node;
            node.set_id(7);
            node.set_name("m0-metadata-only");
            node.set_type(ChakraProtoMsg::METADATA_NODE);
            output.write(node);
        }
        Chakra::ETFeeder feeder(argv[1]);
        auto node = feeder.getNextIssuableNode();
        if (!node || node->getChakraNode()->id() != 7 ||
            node->getChakraNode()->name() != "m0-metadata-only" ||
            node->type() != ChakraProtoMsg::METADATA_NODE)
        {
            throw std::runtime_error("Chakra reader did not return the probe node");
        }
        feeder.removeNode(7);
        if (feeder.hasNodesToIssue())
        {
            throw std::runtime_error("Unexpected extra Chakra node");
        }
        AstraSim::HardwareResource resources(1);
        if (resources.num_npus != 1 || resources.num_in_flight_gpu_comp_ops != 0 ||
            resources.num_in_flight_gpu_comm_ops != 0 ||
            resources.num_in_flight_cpu_ops != 0)
        {
            throw std::runtime_error("Unexpected initial ASTRA resource state");
        }
        const auto ub_type = ns3::UbApp::GetTypeId().GetName();
        const auto now_ns = ns3::Simulator::Now().GetNanoSeconds();
        if (ub_type != "ns3::UbApp" || now_ns != 0)
        {
            throw std::runtime_error("Unexpected UB type or initial ns-3 clock");
        }
        std::cout << "chakra_nodes_read=1\nastra_resource_npus=1\nub_type=" << ub_type
                  << "\nns3_now_ns=" << now_ns << "\nsimulation_events_executed=0\n";
        dl_iterate_phdr(PrintLoadedLibrary, nullptr);
        ns3::Simulator::Destroy();
        std::cout << "PASS: link and reader probe only\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
