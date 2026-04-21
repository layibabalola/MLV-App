#ifndef TEST_RUNTIME_H
#define TEST_RUNTIME_H

#include <QByteArray>
#include <QThreadPool>

namespace test_runtime {

inline void force_single_threaded_pipeline()
{
    qputenv("MLVAPP_FORCE_THREADS", QByteArrayLiteral("1"));
    qputenv("OMP_NUM_THREADS", QByteArrayLiteral("1"));
    qputenv("OMP_DYNAMIC", QByteArrayLiteral("FALSE"));
    QThreadPool::globalInstance()->setMaxThreadCount(1);
}

} // namespace test_runtime

#endif
