#ifndef MLV_APP_TEST_ARTIFACTS_H
#define MLV_APP_TEST_ARTIFACTS_H

#include <map>
#include <string>

namespace test_artifacts {

void clear();
void record(const std::string & key, const std::string & value);
const std::map<std::string, std::string> & all();
bool write_json(const std::string & path, std::string * error_message);

} // namespace test_artifacts

#endif
