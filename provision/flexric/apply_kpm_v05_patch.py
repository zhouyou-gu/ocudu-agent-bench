#!/usr/bin/env python3
"""Apply the benchmark-owned OCUDU KPM v05 compatibility layer to FlexRIC."""

from __future__ import annotations

import pathlib
import re
import shutil
import sys


def replace_once(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected text not found in {path}: {old}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_v05_source_branch(path: pathlib.Path, v03_source: str, v05_source: str) -> None:
    text = path.read_text(encoding="utf-8")
    if 'KPM_VERSION STREQUAL "KPM_V5_00"' in text:
        return
    old_single = f'elseif(KPM_VERSION STREQUAL "KPM_V3")\n  set(KPM_VERSION_SRC {v03_source})\nelse()'
    if old_single in text:
        new_single = (
            f'elseif(KPM_VERSION STREQUAL "KPM_V3")\n'
            f'  set(KPM_VERSION_SRC {v03_source})\n'
            f'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n'
            f'  set(KPM_VERSION_SRC {v05_source})\n'
            f'else()'
        )
        path.write_text(text.replace(old_single, new_single, 1), encoding="utf-8")
        return
    source_pattern = re.escape(v03_source)
    pattern = (
        r'elseif\(KPM_VERSION STREQUAL "KPM_V3(?:_00)?"\)\n'
        r'(?P<body>\s*set\(KPM_VERSION_SRC(?:\s+|\n\s*)'
        + source_pattern
        + r'(?:\s*\n\s*\))?)\n'
        r'else\(\)'
    )
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"expected KPM v3 source branch not found in {path}")
    body = match.group("body")
    if "\n" in body:
        indent = re.match(r"(\s*)set", body).group(1)  # type: ignore[union-attr]
        v05_body = f'{indent}set(KPM_VERSION_SRC\n{indent}        {v05_source}\n{indent}        )'
    else:
        indent = re.match(r"(\s*)set", body).group(1)  # type: ignore[union-attr]
        v05_body = f"{indent}set(KPM_VERSION_SRC {v05_source})"
    replacement = match.group(0).replace("else()", f'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n{v05_body}\nelse()')
    text = text[: match.start()] + replacement + text[match.end() :]
    path.write_text(text, encoding="utf-8")


def add_v05_object_branch(path: pathlib.Path, target: str, include_dir: str) -> None:
    text = path.read_text(encoding="utf-8")
    if 'KPM_VERSION STREQUAL "KPM_V5_00"' in text:
        return
    old = (
        f'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n'
        f'  target_include_directories({target} PRIVATE "../../../sm/kpm_sm/kpm_sm_v03.00/ie/asn")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n\n'
        f'else()'
    )
    new = (
        f'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n'
        f'  target_include_directories({target} PRIVATE "../../../sm/kpm_sm/kpm_sm_v03.00/ie/asn")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n\n'
        f'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n'
        f'  target_include_directories({target} PRIVATE "{include_dir}")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n\n'
        f'else()'
    )
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_v05_public_object_branch(path: pathlib.Path, target: str, include_dir: str) -> None:
    text = path.read_text(encoding="utf-8")
    if 'KPM_VERSION STREQUAL "KPM_V5_00"' in text:
        return
    old = (
        f'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n'
        f'  target_include_directories({target} PUBLIC "../../../sm/kpm_sm/kpm_sm_v03.00/ie/asn")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n'
        f'else()'
    )
    new = (
        f'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n'
        f'  target_include_directories({target} PUBLIC "../../../sm/kpm_sm/kpm_sm_v03.00/ie/asn")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n'
        f'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n'
        f'  target_include_directories({target} PUBLIC "{include_dir}")\n'
        f'  target_compile_options({target} PRIVATE "-DKPM_V3_00")\n'
        f'else()'
    )
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def force_unaligned_per_for_kpm_v05_decoder(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace("ATS_ALIGNED_BASIC_PER", "ATS_UNALIGNED_BASIC_PER")
    text = text.replace("ATS_ALIGNED_BASIC_PER syntax", "ATS_UNALIGNED_BASIC_PER syntax")
    path.write_text(text, encoding="utf-8")


def add_ocudu_kpm_v05_decoder_fallback(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "ocudu_kpm_v05_decode_to_jsonl" in text:
        return
    text = text.replace(
        "#include <stdlib.h>\n",
        "#include <stdlib.h>\n#include <errno.h>\n#include <fcntl.h>\n#include <sys/wait.h>\n#include <unistd.h>\n",
        1,
    )
    helper = r'''
static int ocudu_kpm_v05_decode_to_jsonl(size_t len, uint8_t const* payload)
{
  const char* out_path = getenv("FLEXRIC_KPM_V05_JSONL");
  if (out_path == NULL || out_path[0] == '\0') {
    return -1;
  }
  char tmp_path[] = "/tmp/ocudu-kpm-v05-XXXXXX";
  int fd = mkstemp(tmp_path);
  if (fd < 0) {
    return -1;
  }
  size_t written = 0;
  while (written < len) {
    ssize_t rc = write(fd, payload + written, len - written);
    if (rc <= 0) {
      close(fd);
      unlink(tmp_path);
      return -1;
    }
    written += (size_t)rc;
  }
  close(fd);

  pid_t pid = fork();
  if (pid < 0) {
    unlink(tmp_path);
    return -1;
  }
  if (pid == 0) {
    int log_fd = open("/tmp/ocudu-kpm-v05-decode.log", O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (log_fd >= 0) {
      dup2(log_fd, STDOUT_FILENO);
      dup2(log_fd, STDERR_FILENO);
      close(log_fd);
    }
    execl("/usr/local/bin/ocudu-kpm-v05-decode",
          "ocudu-kpm-v05-decode",
          tmp_path,
          out_path,
          (char*)NULL);
    _exit(127);
  }

  int status = 0;
  while (waitpid(pid, &status, 0) < 0) {
    if (errno != EINTR) {
      unlink(tmp_path);
      return -1;
    }
  }
  unlink(tmp_path);
  return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

'''
    text = text.replace("\nkpm_event_trigger_def_t kpm_dec_event_trigger_asn", "\n" + helper + "kpm_event_trigger_def_t kpm_dec_event_trigger_asn", 1)
    old = (
        "  const enum asn_transfer_syntax syntax = ATS_UNALIGNED_BASIC_PER;\n"
        "  const asn_dec_rval_t rval = asn_decode(NULL, syntax, &asn_DEF_E2SM_KPM_IndicationMessage, (void**)&pdu, ind_msg, len);\n"
        "  assert(rval.code == RC_OK && \"Are you sending data in ATS_UNALIGNED_BASIC_PER syntax?\");"
    )
    new = (
        "  const enum asn_transfer_syntax syntax = ATS_UNALIGNED_BASIC_PER;\n"
        "  const asn_dec_rval_t rval = asn_decode(NULL, syntax, &asn_DEF_E2SM_KPM_IndicationMessage, (void**)&pdu, ind_msg, len);\n"
        "  if (rval.code != RC_OK) {\n"
        "    ASN_STRUCT_FREE_CONTENTS_ONLY(asn_DEF_E2SM_KPM_IndicationMessage, pdu);\n"
        "    free(pdu);\n"
        "    if (ocudu_kpm_v05_decode_to_jsonl(len, ind_msg) == 0) {\n"
        "      ret.type = FORMAT_1_INDICATION_MESSAGE;\n"
        "      return ret;\n"
        "    }\n"
        "    assert(rval.code == RC_OK && \"Are you sending data in ATS_UNALIGNED_BASIC_PER syntax?\");\n"
        "  }"
    )
    if old not in text:
        raise RuntimeError(f"expected KPM indication decode assertion not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: apply_kpm_v05_patch.py FLEXRIC_ROOT OCUDU_ASN_ROOT", file=sys.stderr)
        return 2
    flexric = pathlib.Path(sys.argv[1])
    ocudu_asn = pathlib.Path(sys.argv[2])
    required = [
        ocudu_asn / "include" / "ocudu" / "asn1" / "e2sm" / "e2sm_kpm_ies.h",
        ocudu_asn / "lib" / "asn1" / "e2sm" / "e2sm_kpm_ies.cpp",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("missing OCUDU generated KPM v05 ASN.1 source(s): " + ", ".join(missing))

    kpm_root = flexric / "src" / "sm" / "kpm_sm"
    src = kpm_root / "kpm_sm_v03.00"
    dst = kpm_root / "kpm_sm_v05.00"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    top_cmake = flexric / "CMakeLists.txt"
    top_text = top_cmake.read_text(encoding="utf-8")
    top_text = top_text.replace(
        'set_property(CACHE KPM_VERSION PROPERTY STRINGS  "KPM_V2_01" "KPM_V2_03" "KPM_V3_00")',
        'set_property(CACHE KPM_VERSION PROPERTY STRINGS  "KPM_V2_01" "KPM_V2_03" "KPM_V3_00" "KPM_V5_00")',
    )
    top_text = top_text.replace(
        'set_property(CACHE KPM_VERSION PROPERTY STRINGS "KPM_V2" "KPM_V3")',
        'set_property(CACHE KPM_VERSION PROPERTY STRINGS "KPM_V2" "KPM_V3" "KPM_V5_00")',
    )
    if 'KPM_V5_00_USES_V3_COMPAT_TYPES' not in top_text:
        top_text = top_text.replace(
            'message(STATUS "Selected KPM Version: ${KPM_VERSION}")',
            'message(STATUS "Selected KPM Version: ${KPM_VERSION}")\n'
            'if(KPM_VERSION STREQUAL "KPM_V5_00")\n'
            '  add_compile_definitions(KPM_V3 KPM_V3_00 KPM_V5_00_USES_V3_COMPAT_TYPES)\n'
            'endif()',
        )
    top_cmake.write_text(top_text, encoding="utf-8")

    root_cmake = kpm_root / "CMakeLists.txt"
    text = root_cmake.read_text(encoding="utf-8")
    if 'KPM_VERSION STREQUAL "KPM_V5_00"' not in text:
        if 'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n  add_subdirectory(kpm_sm_v03.00)\nelse()' in text:
            text = text.replace(
                'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n  add_subdirectory(kpm_sm_v03.00)\nelse()',
                'elseif(KPM_VERSION STREQUAL "KPM_V3_00")\n'
                '  add_subdirectory(kpm_sm_v03.00)\n'
                'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n'
                '  add_subdirectory(kpm_sm_v05.00)\n'
                'else()',
            )
        else:
            text = text.replace(
                'elseif(KPM_VERSION STREQUAL "KPM_V3")\n  add_subdirectory(kpm_sm_v03.00)\nelse()',
                'elseif(KPM_VERSION STREQUAL "KPM_V3")\n'
                '  add_subdirectory(kpm_sm_v03.00)\n'
                'elseif(KPM_VERSION STREQUAL "KPM_V5_00")\n'
                '  add_subdirectory(kpm_sm_v05.00)\n'
                'else()',
            )
        root_cmake.write_text(text, encoding="utf-8")

    id_header = dst / "kpm_sm_id.h"
    replace_once(id_header, "static const uint16_t SM_KPM_REV = 3;", "static const uint16_t SM_KPM_REV = 5;")
    id_text = id_header.read_text(encoding="utf-8")
    id_text = re.sub(r"R003-v03\.00", "R003-v05.00", id_text)
    id_text = re.sub(r"53148\.1\.3\.2\.2", "53148.1.5.2.2", id_text)
    id_header.write_text(id_text, encoding="utf-8")

    add_v05_source_branch(
        flexric / "src" / "xApp" / "CMakeLists.txt",
        "../sm/kpm_sm/kpm_sm_v03.00/ie/kpm_data_ie.c",
        "../sm/kpm_sm/kpm_sm_v05.00/ie/kpm_data_ie.c",
    )
    add_v05_source_branch(
        flexric / "src" / "xApp" / "swig" / "CMakeLists.txt",
        "../../sm/kpm_sm/kpm_sm_v03.00/ie/kpm_data_ie.c",
        "../../sm/kpm_sm/kpm_sm_v05.00/ie/kpm_data_ie.c",
    )
    add_v05_public_object_branch(
        flexric / "src" / "lib" / "sm" / "dec" / "CMakeLists.txt",
        "sm_common_dec_asn_obj_kpm",
        "../../../sm/kpm_sm/kpm_sm_v05.00/ie/asn",
    )
    add_v05_public_object_branch(
        flexric / "src" / "lib" / "sm" / "enc" / "CMakeLists.txt",
        "sm_common_enc_asn_obj_kpm",
        "../../../sm/kpm_sm/kpm_sm_v05.00/ie/asn",
    )
    add_v05_object_branch(
        flexric / "src" / "lib" / "3gpp" / "dec" / "CMakeLists.txt",
        "3gpp_derived_ie_dec_asn_obj_kpm",
        "../../../sm/kpm_sm/kpm_sm_v05.00/ie/asn",
    )
    add_v05_object_branch(
        flexric / "src" / "lib" / "3gpp" / "enc" / "CMakeLists.txt",
        "3gpp_derived_ie_enc_asn_obj_kpm",
        "../../../sm/kpm_sm/kpm_sm_v05.00/ie/asn",
    )
    force_unaligned_per_for_kpm_v05_decoder(dst / "dec" / "kpm_dec_asn.c")
    add_ocudu_kpm_v05_decoder_fallback(dst / "dec" / "kpm_dec_asn.c")

    bridge = dst / "ie" / "ocudu_kpm_v05_bridge"
    bridge.mkdir(parents=True, exist_ok=True)
    for path in required:
        out = bridge / path.name
        shutil.copy2(path, out)
    (bridge / "README.md").write_text(
        "OCUDU-generated E2SM-KPM v05 ASN.1 sources copied into the FlexRIC image. "
        "The benchmark requires decoded KPM records at runtime; raw E2 indication counts do not pass conformance.\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
