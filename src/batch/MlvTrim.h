#ifndef MLVTRIM_H
#define MLVTRIM_H

class QCoreApplication;

class MlvTrim
{
public:
    static int run(QCoreApplication &app);

private:
    MlvTrim() = delete;
};

#endif
