#pragma once
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>

namespace moe_ub {
enum class MessageCategory:uint8_t { Business,AttachedMetadata,DispatchCount,CombineFlag,LoadReady };
inline constexpr std::array<const char*,5> MessageCategoryNames={"business_data","attached_metadata","dispatch_count","combine_flag","load_ready"};
inline const char* CategoryName(MessageCategory value) { return MessageCategoryNames.at(static_cast<size_t>(value)); }
inline MessageCategory ParseCategory(const std::string& name) {
    for(size_t i=0;i<MessageCategoryNames.size();++i) if(name==MessageCategoryNames[i]) return static_cast<MessageCategory>(i);
    throw std::runtime_error("Unknown application message category");
}
inline MessageCategory MetadataCategory(const std::string& kind,const std::string& role) {
    if(role=="ready") return MessageCategory::LoadReady;
    if(role=="status" && kind=="dispatch") return MessageCategory::DispatchCount;
    if(role=="status" && kind=="combine") return MessageCategory::CombineFlag;
    if(role=="data") return MessageCategory::AttachedMetadata;
    throw std::runtime_error("Unclassified application metadata message");
}
}
