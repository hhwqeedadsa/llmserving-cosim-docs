#pragma once

#include "extern/helper/json/json.hpp"
#include <istream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace moe_ub
{
// A lossless in-memory representation, not a new disk schema. Large child plans
// remain serialized until their turn in validation. Marker indices are private
// to this parser and are checked before use; source integers/strings cannot
// masquerade as captured objects.
struct PackedPlanJson
{
    nlohmann::json data;
    std::vector<std::string> layers;

    void CheckMarkers() const
    {
        if (layers.empty()) return;
        const auto& markers = data.at("layers");
        if (!markers.is_array() || markers.size() != layers.size())
            throw std::runtime_error("Layer plan: invalid packed child array");
        for (size_t i = 0; i < layers.size(); ++i)
            if (!markers[i].is_number_unsigned() || markers[i] != i)
                throw std::runtime_error("Layer plan: invalid packed child marker");
    }
    void Restore()
    {
        CheckMarkers();
        for (size_t i = 0; i < layers.size(); ++i)
            data["layers"][i] = nlohmann::json::parse(layers[i]);
        layers.clear();
    }
};

namespace detail
{
class PackedPlanSax
{
    using Json = nlohmann::json;
    using Dom = nlohmann::detail::json_sax_dom_parser<Json>;
    PackedPlanJson& result_;
    Dom root_;
    Json child_;
    std::unique_ptr<Dom> child_parser_;
    size_t depth_ = 0;
    std::string field_;
    Dom& Active() { return child_parser_ ? *child_parser_ : root_; }
  public:
    explicit PackedPlanSax(PackedPlanJson& result) : result_(result), root_(result.data) {}
    bool null() { return Active().null(); }
    bool boolean(bool value) { return Active().boolean(value); }
    bool number_integer(Json::number_integer_t value) { return Active().number_integer(value); }
    bool number_unsigned(Json::number_unsigned_t value) { return Active().number_unsigned(value); }
    bool number_float(Json::number_float_t value, const std::string& text) { return Active().number_float(value, text); }
    bool string(std::string& value) { return Active().string(value); }
    bool binary(Json::binary_t& value) { return Active().binary(value); }
    bool key(std::string& value)
    {
        if (depth_ == 1) field_ = value;
        return Active().key(value);
    }
    bool start_object(size_t count)
    {
        if (depth_ == 2 && field_ == "layers" && !child_parser_)
            child_parser_ = std::make_unique<Dom>(child_);
        ++depth_;
        return Active().start_object(count);
    }
    bool end_object()
    {
        Active().end_object();
        --depth_;
        if (depth_ == 2 && child_parser_)
        {
            result_.layers.push_back(child_.dump());
            child_parser_.reset(); child_ = nullptr;
            root_.number_unsigned(result_.layers.size() - 1);
        }
        return true;
    }
    bool start_array(size_t count) { ++depth_; return Active().start_array(count); }
    bool end_array() { --depth_; return Active().end_array(); }
    bool parse_error(size_t position, const std::string& token, const Json::exception& error)
    {
        return root_.parse_error(position, token, error);
    }
};
}

inline PackedPlanJson ParsePackedPlanJson(std::istream& stream)
{
    PackedPlanJson result;
    detail::PackedPlanSax parser(result);
    if (!nlohmann::json::sax_parse(stream, &parser))
        throw std::runtime_error("Layer plan: JSON parsing failed");
    result.CheckMarkers();
    return result;
}
}
