#ifndef MLV_APP_HASH_HELPERS_H
#define MLV_APP_HASH_HELPERS_H

#include <cstddef>
#include <string>

class QString;

std::string sha256_bytes(const void * data, std::size_t size);
std::string sha256_string(const std::string & value);
std::string sha256_qstring(const QString & value);
std::string sha256_file(const QString & path);

#endif
