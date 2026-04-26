#ifndef MLVAPP_FORCE_SINGLE_THREAD_H
#define MLVAPP_FORCE_SINGLE_THREAD_H

#ifdef __cplusplus
extern "C" {
#endif

void mlvapp_force_singlethread_init(void);
int mlvapp_is_forced_singlethread(void);
void mlvapp_force_singlethread_reset_for_test(void);

#ifdef __cplusplus
}
#endif

#endif // MLVAPP_FORCE_SINGLE_THREAD_H
