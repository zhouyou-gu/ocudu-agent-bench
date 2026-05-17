#include "ocudu/asn1/e2sm/e2sm_kpm_ies.h"
#include "ocudu/adt/byte_buffer.h"

#include <cstdint>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace asn1::e2sm;

static std::string escape_json(const std::string& value)
{
  std::ostringstream out;
  for (char ch : value) {
    switch (ch) {
      case '\\':
        out << "\\\\";
        break;
      case '"':
        out << "\\\"";
        break;
      case '\n':
        out << "\\n";
        break;
      case '\r':
        out << "\\r";
        break;
      case '\t':
        out << "\\t";
        break;
      default:
        out << ch;
    }
  }
  return out.str();
}

static std::string meas_name_at(const e2sm_kpm_ind_msg_format1_s& msg, size_t index)
{
  if (index >= msg.meas_info_list.size()) {
    return "measurement_" + std::to_string(index);
  }
  const auto& meas_type = msg.meas_info_list[index].meas_type;
  if (meas_type.type() == meas_type_c::types::meas_name) {
    return meas_type.meas_name().to_string();
  }
  if (meas_type.type() == meas_type_c::types::meas_id) {
    return "meas_id_" + std::to_string(meas_type.meas_id());
  }
  return "measurement_" + std::to_string(index);
}

static void write_measurement_json(std::ostream& out,
                                   const e2sm_kpm_ind_msg_format1_s& msg,
                                   const meas_record_item_c& record,
                                   size_t index)
{
  out << "{\"name\":\"" << escape_json(meas_name_at(msg, index)) << "\",";
  switch (record.type().value) {
    case meas_record_item_c::types_opts::integer:
      out << "\"type\":\"integer\",\"value\":" << record.integer();
      break;
    case meas_record_item_c::types_opts::real:
      out << "\"type\":\"real\",\"value\":" << record.real().value;
      break;
    case meas_record_item_c::types_opts::no_value:
      out << "\"type\":\"no_value\",\"value\":null";
      break;
    case meas_record_item_c::types_opts::not_satisfied:
      out << "\"type\":\"not_satisfied\",\"value\":null";
      break;
    default:
      out << "\"type\":\"unknown\",\"value\":null";
      break;
  }
  out << "}";
}

static void write_format1_json(std::ostream& out, const e2sm_kpm_ind_msg_format1_s& msg)
{
  out << "{\"decoded_by\":\"ocudu-generated-asn1-cpp\","
      << "\"kpm_version\":\"E2SM-KPM-R003-v05.00\","
      << "\"format\":\"ind_msg_format1\","
      << "\"measurements\":[";
  bool first = true;
  for (size_t row = 0; row < msg.meas_data.size(); ++row) {
    const auto& item = msg.meas_data[row];
    for (size_t col = 0; col < item.meas_record.size(); ++col) {
      if (!first) {
        out << ",";
      }
      first = false;
      write_measurement_json(out, msg, item.meas_record[col], col);
    }
  }
  out << "]}";
}

int main(int argc, char** argv)
{
  if (argc < 2 || argc > 3) {
    std::cerr << "usage: ocudu-kpm-v05-decode INDICATION_MESSAGE_BIN [OUT_JSONL]\n";
    return 2;
  }

  std::ifstream input(argv[1], std::ios::binary);
  if (!input) {
    std::cerr << "failed to open input payload\n";
    return 2;
  }
  std::vector<uint8_t> data((std::istreambuf_iterator<char>(input)), {});
  if (data.empty()) {
    std::cerr << "empty input payload\n";
    return 2;
  }

  ocudu::byte_buffer buffer;
  for (uint8_t byte : data) {
    if (!buffer.append(byte)) {
      std::cerr << "failed to append input payload byte\n";
      return 2;
    }
  }

  asn1::cbit_ref       bref(buffer);
  e2sm_kpm_ind_msg_s   msg;
  const asn1::OCUDUASN_CODE rc = msg.unpack(bref);
  if (rc != asn1::OCUDUASN_SUCCESS) {
    std::cerr << "OCUDU KPM v05 indication decode failed\n";
    return 1;
  }

  std::ostringstream decoded;
  switch (msg.ind_msg_formats.type().value) {
    case e2sm_kpm_ind_msg_s::ind_msg_formats_c_::types_opts::ind_msg_format1:
      write_format1_json(decoded, msg.ind_msg_formats.ind_msg_format1());
      break;
    default:
      decoded << "{\"decoded_by\":\"ocudu-generated-asn1-cpp\","
              << "\"kpm_version\":\"E2SM-KPM-R003-v05.00\","
              << "\"format\":\"" << msg.ind_msg_formats.type().to_string() << "\","
              << "\"measurements\":[]}";
      break;
  }

  const std::string line = decoded.str();
  if (argc == 3) {
    std::ofstream output(argv[2], std::ios::app);
    if (!output) {
      std::cerr << "failed to open output JSONL\n";
      return 2;
    }
    output << line << "\n";
  }
  std::cout << line << "\n";
  return 0;
}
