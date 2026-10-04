#include "packed_plan_json.hh"
#include <iostream>
#include <sstream>
#include <stdexcept>

using Json = nlohmann::json;
void Require(bool ok) { if (!ok) throw std::runtime_error("Packed JSON assertion failed"); }
int main()
{
    for (const std::string input : {
        R"({"layers":[{"input":{"layers":[1,2]},"nodes":[{"s":"中文\\\""},true,null,1.5,-2,18446744073709551615]},{}],"schema_version":"after"})",
        R"({"schema_version":"before","model":{"layers":[{"id":1}]},"layers":[{"nested":{"layers":[{}]}}],"tail":[1,2]})",
        R"({"layers":[],"input":{}})", R"({"input":{"layers":[{}]}})"})
    {
        std::istringstream stream(input);
        auto parsed = moe_ub::ParsePackedPlanJson(stream);
        const auto expected = Json::parse(input);
        if (expected.contains("layers") && !expected["layers"].empty())
        {
            Require(parsed.layers.size() == expected["layers"].size());
            for (size_t i = 0; i < parsed.layers.size(); ++i)
                Require(Json::parse(parsed.layers[i]) == expected["layers"][i]);
        }
        parsed.Restore();
        Require(parsed.data == expected);
    }
    for (const std::string input : {R"({"layers":[{},0]})", R"({"layers":["{}",{}]})",
                                    R"({"layers":[{},{}])", R"({"layers":[{}]} trailing)"})
    {
        bool failed = false;
        try { std::istringstream stream(input); auto parsed = moe_ub::ParsePackedPlanJson(stream); }
        catch (const std::exception&) { failed = true; }
        Require(failed);
    }
    // Primitive-only children remain invalid source values, never reconstructed
    // from marker text; production semantic validation must reject this shape.
    std::istringstream invalid(R"({"layers":[0,"{}"]})");
    auto parsed = moe_ub::ParsePackedPlanJson(invalid);
    Require(parsed.layers.empty() && parsed.data == Json::parse(R"({"layers":[0,"{}"]})"));
    std::cout << "PASS: lossless child packing, field order, escapes, nested arrays and damaged input rejection\n";
}
