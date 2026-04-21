TEMPLATE = subdirs
CONFIG += ordered

receipt.file = $$PWD/fuzz_receipt_loader.pro
lj92.file = $$PWD/fuzz_lj92.pro
mlv.file = $$PWD/fuzz_mlv_open.pro

SUBDIRS += receipt lj92 mlv
