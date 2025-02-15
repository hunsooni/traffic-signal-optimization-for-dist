# -*- coding: utf-8 -*-
#
#  use "policy/off_ppoTF2.py" as a policy
#  [$] conda activate opt
#  [$] python run_dist_considered.py  --mode train --map doan --target-TL "SA 101" --model-save-period 1 --mem-len 10 --epoch 1 --num-concurrent-env 2  --output-home zzz
#  [$] python run_dist_considered.py  --mode simulate --map doan --target-TL "SA 101" --output-home zzz
#  [$] python run_dist_considered.py  --mode test --map doan --target-TL "SA 101" --model-save-period 1 --mem-len 10 --model-num 0  --output-home zzz
#  [$] python run_dist_considered.py  --mode test --map doan --target-TL "SA 101" --model-save-period 1
#                                     --mem-len 10 --model-num 0  --output-home zzz
#                                     --result-comp True --comp-total-only True


#  [$] python run_dist_considered.py --mode train --map doan --target-TL "SA 101, SA 104" --model-save-period 1 --mem-len 10 --epoch 1


#
import argparse
import datetime
import numpy as np
import os

from multiprocessing import Process, Pipe
from threading import Thread
import tensorflow as tf

import pandas as pd
import shutil
import time
import sys
import copy


#os.environ['CUDA_VISIBLE_DEVICES'] = "-1" # "0" # "0,1,2"
#from tensorflow.python.client import device_lib
#device_lib.list_local_devices()

if 1:
    import sys
    if 'SALT_HOME' in os.environ:
        tools = os.path.join(os.environ['SALT_HOME'], 'tools')
        sys.path.append(tools)

        tools_libsalt = os.path.join(os.environ['SALT_HOME'], 'tools/libsalt')
        sys.path.append(tools_libsalt)
    else:
        sys.exit("Please declare the environment variable 'SALT_HOME'")



def configure_gpu():
    
    gpus = tf.config.experimental.list_physical_devices('GPU')
    if gpus:
        try:
            # Currently, memory growth needs to be the same across GPUs
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
            logical_gpus = tf.config.experimental.list_logical_devices('GPU')
            print(len(gpus), "Physical GPUs,", len(logical_gpus), "Logical GPUs")
        except RuntimeError as e:
            # Memory growth must be set before GPUs have been initialized
            print(e)

configure_gpu()



import libsalt

from DebugConfiguration import DBG_OPTIONS, waitForDebug

from env.off_ppo.SaltEnvUtil import appendPhaseRewards, gatherTsoOutputInfo

from env.off_ppo.SaltEnvUtil import appendTsoOutputInfoSignal, appendTsoOutputInfo, initTsoOutputInfo

#from env.off_ppo.SaltEnvUtil import copyScenarioFiles
from env.off_ppo.SaltEnvUtil import getSaRelatedInfo
from env.off_ppo.SaltEnvUtil import getSimulationStartStepAndEndStep
from env.off_ppo.SaltEnvUtil import makePosssibleSaNameList

from env.off_ppo.SappoRewardMgmt import SaltRewardMgmtV3

from policy.off_ppoTF2 import PPOAgentTF2 #from policy.ppoTF2 import PPOAgentTF2

from TSOConstants import _FN_PREFIX_, _RESULT_COMP_, _RESULT_COMPARE_SKIP_
from TSOUtil import addArgumentsToParser
from TSOUtil import appendLine
# from TSOUtil import appendTsoOutputInfo
from TSOUtil import calculateImprovementRate
from TSOUtil import checkTrafficEnvironment
from TSOUtil import convertSaNameToId
from TSOUtil import copyScenarioFiles
from TSOUtil import findOptimalModelNum
from TSOUtil import getOutputDirectoryRoot
# from TSOUtil import initTsoOutputInfo
from TSOUtil import makeConfigAndProblemVar
from TSOUtil import makePosssibleSaNameList
from TSOUtil import removeWhitespaceBtnComma
from TSOUtil import writeLine


##
# DBG_OPTIONS.USE_IMPORT_FROM_RUN_OFF_PPO_SINGLE = True
## use codes in run_off_ppo_single.py if USE_IMPORT_FROM_RUN_OFF_PPO_SINGLE is True
## I copied the code from run_off_ppo_single.py (2023.07.13)
## This is to prepare for when the code (run_off_ppo_single.py) is changed.
##

###
### this file is based on run_off_ppo_single.py
###

# from run_off_ppo_single import parseArgument
if DBG_OPTIONS.USE_IMPORT_FROM_RUN_OFF_PPO_SINGLE:
    from run_off_ppo_single import makeDirectories
    # from run_off_ppo_single import createEnvironment

    # from run_off_ppo_single import makeLoadModelFnPrefix
    # ----- modify it to work with distributed learning
    # ----- use getOutputDirectoryRoot(args) instead of args.io_home to construct path

    ##### run_off_ppo_single.py
    # from run_off_ppo_single import one_hot
    # from run_off_ppo_single import StateAugmentation
    from run_off_ppo_single import Agent
    from run_off_ppo_single import Env
    # from run_off_ppo_single import isolated # modify it to work with DL ... see isolatedDist()
    from run_off_ppo_single import IsolatedEnv
    from run_off_ppo_single import run_test_episode
    from run_off_ppo_single import run_valid_episode
    from run_off_ppo_single import run_multi_thread

    # from run_off_ppo_single import compareResultAndStore
    # ----- modify it to work with distributed learning
    # ----- use getOutputDirectoryRoot(args) instead of args.io_home to construct path

    # from run_off_ppo_single import __printImprovementRate
else:
    from env.off_ppo.SappoEnv import SaltSappoEnvV3

    def makeDirectories(dir_name_list):
        '''
        create directories
        :param dir_name_list:
        :return:
        '''
        for dir_name in dir_name_list:
            os.makedirs(dir_name, exist_ok=True)
        return


    def one_hot(indices, depth, on_value=1.0, off_value=0.0):
        scalar = False
        if not isinstance(indices, (list, tuple, np.ndarray)):
            scalar = True
            indices = [indices]

        length = len(indices)
        one_hot = np.ones((length, depth)) * off_value
        one_hot[np.arange(length), indices] = on_value

        if scalar: one_hot = one_hot[0]

        return one_hot


    class StateAugmentation:

        def __init__(self, step_size, on_value=1.0, off_value=0.0):
            self.step_size = step_size
            self.on_value = on_value
            self.off_value = off_value
            self.time_step = 0

        def reset(self):
            self.time_step = 0

        def augment(self, state):
            time_encoded = one_hot(self.time_step, depth=self.step_size, on_value=self.on_value,
                                   off_value=self.off_value)
            # print('state:', state)
            # print('time_encoded:', time_encoded)
            state = np.concatenate([state, time_encoded], axis=-1)

            self.time_step += 1

            return state


    class Agent:

        def __init__(self, env_name, agent_num, action_sizes, state_sizes, ppo_config, problem_var, target_sas, args):

            self._init_holder(agent_num, action_sizes)
            self.env_name = env_name
            self.ppo_config = ppo_config
            self.problem_var = problem_var

            self.args = args

            self.ppo_agent = []
            for i in range(agent_num):
                agent = PPOAgentTF2(env_name, ppo_config, action_sizes[i], state_sizes[i],
                                    target_sas[i].strip().replace(' ', '_'))
                self.ppo_agent.append(agent)

        def _init_holder(self, agent_num, action_sizes):

            actions, logps = [], []

            for i in range(agent_num):
                actions.append(list(0 for _ in range(action_sizes[i])))
                logps.append([0])

            self._action_holder = actions
            self._logp_holder = logps

        def act(self, state, info, sampling=True):

            action_holder = copy.deepcopy(self._action_holder)  # For multi-treading
            logp_holder = copy.deepcopy(self._logp_holder)

            idx_of_act_sa = info['idx_of_act_sa']

            for i in idx_of_act_sa:
                obs = state[i]
                action, logp, mu, std = self.ppo_agent[i].action(obs, sampling)

                self._action_holder[i] = action[0]
                self._logp_holder[i] = logp[0]

            action_holder = copy.deepcopy(self._action_holder)
            logp_holder = copy.deepcopy(self._logp_holder)

            return action_holder, logp_holder

        def store(self, current_state, action, reward, new_state, done, logp, info):

            idx_of_act_sa = info['idx_of_act_sa']

            for i in idx_of_act_sa:

                if current_state[i] is not None and new_state[i] is not None:
                    self.ppo_agent[i].memory.store(current_state[i],
                                                   action[i],
                                                   reward[i],
                                                   new_state[i],
                                                   done,
                                                   logp[i])

        def train(self):

            for agent in self.ppo_agent:
                agent.replayNew()

        def getMemorySize(self):
            memory_size = self.ppo_agent[0].memory.getSize()
            return memory_size

        def save_agent(self, trial):
            args = self.args
            problem_var = self.problem_var
            fn_prefix = "{}/model/{}/{}-{}-trial_{}".format(args.io_home, args.method, args.method.upper(), problem_var,
                                                            trial)
            for agent in self.ppo_agent:
                agent.saveModel(fn_prefix)

        def load_agent(self, trial):
            args = self.args
            problem_var = self.problem_var
            fn_prefix = "{}/model/{}/{}-{}-trial_{}".format(args.io_home, args.method, args.method.upper(), problem_var,
                                                            trial)
            for agent in self.ppo_agent:
                agent.loadModel(fn_prefix)


    class Env(SaltSappoEnvV3):

        def __init__(self, args):

            args = copy.deepcopy(args)
            # args.scenario_file_path = f"{args.scenario_file_path}/{args.map}/{args.map}_{args.mode}_{args.scenario}.scenario.json"
            if DBG_OPTIONS.YJLEE:
                args.scenario_file_path = f"{args.scenario_file_path}/{args.map}/{args.map}_{args.mode}_{args.scenario}.scenario.json"

            start_time, end_time = getSimulationStartStepAndEndStep(args)
            trial_len = end_time - start_time

            args.start_time = start_time
            args.end_time = end_time

            super(Env, self).__init__(args)
            self._init_holder()

            self.step_size = []
            self.state_augment = []
            for sa_cycle in self.sa_cycle_list:  # SappoEnv.py Line 156
                # print('sa_cycle:', sa_cycle)
                # step_size = int(trial_len/(sa_cycle * args.control_cycle))
                # step_size = int(np.ceil(trial_len/(sa_cycle * args.control_cycle)))
                step_size = self._calculateStepSize(trial_len, sa_cycle)
                # print('step_size:', step_size)
                aug = StateAugmentation(step_size, on_value=1.0, off_value=-1.0)

                # print('step_size', step_size)
                self.step_size.append(step_size)
                self.state_augment.append(aug)

        def _calculateStepSize(self, trial_len, sa_cycle):
            return int(np.ceil(trial_len / (sa_cycle * args.control_cycle)))

        def get_agent_configuration(self):

            env_name = self.env_name
            args = self.args
            ppo_config, problem_var = makeConfigAndProblemVar(self.args)
            agent_num = self.agent_num

            action_sizes = []
            state_sizes = []
            target_sas = []
            for i in range(agent_num):
                target_sa = self.sa_name_list[i]

                is_train_target = self.isTrainTarget(target_sa)
                ppo_config["is_train"] = is_train_target

                state_space = self.sa_obj[target_sa]['state_space']
                action_space = self.sa_obj[target_sa]['action_space']

                ##-- TF 2.x : ppo_continuous_hs,py
                action_size = action_space.shape[0]
                # print('action_size', action_size)
                # state_size = (state_space,)
                state_size = (state_space + self.step_size[i],)

                action_sizes.append(action_size)
                state_sizes.append(state_size)
                target_sas.append(target_sa)

            return env_name, agent_num, action_sizes, state_sizes, ppo_config, problem_var, target_sas, args

        def _init_holder(self):

            # To store reward history of each episode
            self.ep_reward_list = []

            # self.current_state = [] # Actually, it is not used. Keep it to maintain consistency with the previous code.

            # actions, logp_ts = [], []
            agent_num = self.agent_num

            discrete_actions = []

            for i in range(agent_num):
                target_sa = self.sa_name_list[i]
                action_space = self.sa_obj[target_sa]['action_space']
                action_size = action_space.shape[0]
                # actions.append(list(0 for _ in range(action_size)))
                # logp_ts.append([0])

                discrete_actions.append(list(0 for _ in range(action_size)))

            self._discrete_action_holder = discrete_actions

        def _reshape_state(self, state, idx_of_act_sa):

            state = copy.deepcopy(state)

            for i in idx_of_act_sa:
                obs = state[i]
                obs = self.state_augment[i].augment(obs)
                obs = obs.reshape(1, -1)  # [1,2,3]  ==> [ [1,2,3] ]
                state[i] = obs

            return state

        def reset(self):

            for aug in self.state_augment: aug.reset()
            self._augmented_state = [None for i in range(self.agent_num)]

            state = super(Env, self).reset()

            idx_of_act_sa = copy.deepcopy(self.idx_of_act_sa)
            augmented_state = self._reshape_state(state, idx_of_act_sa)
            for i in idx_of_act_sa:
                self._augmented_state[i] = augmented_state[i]

            augmented_state = copy.deepcopy(self._augmented_state)
            info = {'idx_of_act_sa': idx_of_act_sa}

            self.episodic_reward = 0
            self.episodic_agent_reward = [0] * self.agent_num

            return augmented_state, info

        def step(self, actions):

            idx_of_act_sa = copy.deepcopy(self.idx_of_act_sa)
            for i in idx_of_act_sa:
                sa_name = self.sa_name_list[i]

                discrete_action = np.clip(actions[i], -1.0, +1.0)
                discrete_action = self.action_mgmt.convertToDiscreteAction(sa_name, discrete_action)
                self._discrete_action_holder[i] = discrete_action

            next_state, reward, done, info = super(Env, self).step(
                self._discrete_action_holder)  # After calling step(), self.idx_of_act_sa is updated.

            idx_of_act_sa = copy.deepcopy(self.idx_of_act_sa)
            augmented_next_state = self._reshape_state(next_state, idx_of_act_sa)

            # update observation
            for i in idx_of_act_sa:
                self._augmented_state[i] = augmented_next_state[i]
                self.episodic_reward += reward[i]
                self.episodic_agent_reward[i] += reward[i]

            next_augmented_state = copy.deepcopy(self._augmented_state)
            info['idx_of_act_sa'] = idx_of_act_sa

            if done:
                self.ep_reward_list.append(self.episodic_reward)
                info['episodic_reward'] = self.episodic_reward
                info['recent_returns'] = self.ep_reward_list[-10:]
                info['ma40_reward'] = np.mean(self.ep_reward_list[-40:])

            return next_augmented_state, reward, done, info


    def isolated(conn, args):

        env = Env(args)

        while True:
            msg = conn.recv()

            if msg[0] == 'get_agent_configuration':
                config = env.get_agent_configuration()
                conn.send(config)

            elif msg[0] == 'reset':
                state, info = env.reset()
                conn.send((state, info))

            elif msg[0] == 'step':
                state, reward, done, info = env.step(msg[1])

                transition = (state, reward, done, info)
                conn.send(transition)

            elif msg[0] == 'close':
                conn.close()
                env.close()
                del env
                break


    class IsolatedEnv():

        def __init__(self, args, max_run=100):

            self.args = copy.deepcopy(args)
            self._max_run = max_run
            self._run = 0
            self._conn = None
            self._env_process = None

            self.ep_reward_list = []

            self._create_env_process()

        def _create_env_process(self):

            if self._env_process is not None: self.close()

            parent_conn, child_conn = Pipe()
            self._conn = parent_conn
            self._env_process = Process(target=isolated, args=(child_conn, self.args))
            # self._env_process.daemon = True
            self._env_process.start()

        def get_agent_configuration(self):

            self._conn.send(('get_agent_configuration',))
            config = self._conn.recv()
            return config

        def reset(self):
            self._run += 1
            if self._run > self._max_run:
                self._run = 0
                self._create_env_process()

            self._conn.send(('reset',))
            state, info = self._conn.recv()

            return state, info

        def step(self, actions):

            self._conn.send(('step', actions))
            state, reward, done, info = self._conn.recv()

            if done:
                self.ep_reward_list.append(info['episodic_reward'])
                info['recent_returns'] = self.ep_reward_list[-10:]
                info['ma40_reward'] = np.mean(self.ep_reward_list[-40:])

            return state, reward, done, info

        def close(self):
            # if self.process.is_alive():
            self._conn.send(('close',))
            self._env_process.join()
            self._conn.close()
            # self.process.terminate()


    def run_train_episode(trial, env, agent):

        current_state, info = env.reset()
        done = False
        while not done:
            action, logp = agent.act(current_state, info, sampling=True)
            next_state, reward, done, info = env.step(action)

            agent.store(current_state, action, reward, next_state, done, logp, info)
            current_state = next_state


    def run_test_episode(trial, env, agent):

        start_time = time.time()
        state, info = env.reset()
        done = False
        while not done:
            action, logp = agent.act(state, info, sampling=False)
            state, reward, done, info = env.step(action)

        end_time = time.time()

        print("Reward in current episode:", info['episodic_reward'])
        print('Recent returns:', info['recent_returns'])
        print("Episode * {} * Avg Reward is ==> {}".format(trial, info['ma40_reward']))
        print("Simulation time :", end_time - start_time)

        return info


    def run_valid_episode(trial, env, agent, best_trial, best_score):

        info = run_test_episode(trial, env, agent)
        score = info['episodic_reward']
        if score > best_score:
            best_trial = trial
            best_score = score

        # print('Best trial:', best_trial, best_score)

        return best_trial, best_score


    def run_multi_thread(trial, envs, agent):

        start_time = time.time()
        num_envs = len(envs)
        threads = []
        for i, env in enumerate(envs):
            thread = Thread(target=run_train_episode, args=(trial * num_envs + i, env, agent))
            thread.start()
            threads.append(thread)

        for thread in threads: thread.join()
        end_time = time.time()

        rewards = [env.ep_reward_list[-1] for env in envs]
        mean = np.mean(rewards)
        std = np.std(rewards)

        print("Episode: {}, Simulation time: {}, Memory Size: {}".format(trial, end_time - start_time,
                                                                         agent.getMemorySize()))
        for i, env in enumerate(envs):
            print('Env:', i)
            print("Reward in current episode:", env.ep_reward_list[-1])
            print('Recent returns:', env.ep_reward_list[-10:])
            print("Avg Reward is ==> {}".format(np.mean(env.ep_reward_list[-40:])))

        return rewards, mean, std


##
##

def parseArgument():
    '''
    argument parsing
    :return:
    '''

    parser = argparse.ArgumentParser()

    parser = addArgumentsToParser(parser)

    args = parser.parse_args()

    args.scenario_file_path = f"{args.scenario_file_path}/{args.map}/{args.map}_{args.mode}.scenario.json"

    # todo : think how often should we update actions
    # if args.action == 'gr':
    #     args.control_cycle = 1

    # to use only exploitation when we do "test"
    if args.mode == 'test':
        args.epsilon = 0.0
        args.epsilon_min = 0.0

    return args


def makeLoadModelFnPrefix(args, problem_var, is_train_target=False):
    '''
    make a prefix of file name which indicates saved trained model parameters

    it should be consistent with LearningDaemonThread::__copyTrainedModel() at DistExecDaemon.py

    v3: we consider cumulative training
    :param args:
    :param problem_var:
    :param is_train_target
    :return:
    '''

    fn_prefix=""

    ## get model num to load
    if args.mode=="train":
        if is_train_target: # i.e., target-TL
            if args.cumulative_training and ( int(args.infer_model_num) >= 0 ) :
                load_model_num = args.model_num
            else:
                return fn_prefix # no need to load pre-trained model
        else: # if is_train_target == False, i.e., infer-TL
            # do not care whether cumulative_training is true or not
            load_model_num = args.infer_model_num
    else: # i.e., args.mode == "test"
        load_model_num = args.model_num


    ## construct file path
    if is_train_target and args.mode=="train":
        assert args.cumulative_training == True, "internal error : it can not happen ... should have already exited from this func "
        fn_path = "{}/model/{}".format(getOutputDirectoryRoot(args), args.method)
    elif args.infer_model_path == ".":
        fn_path = "{}/model/{}".format(getOutputDirectoryRoot(args), args.method)
    else:
        fn_path = args.infer_model_path

    fn_prefix = "{}/{}-{}-trial_{}".format(fn_path, args.method.upper(), problem_var, load_model_num)

    return fn_prefix





##########
def __processStatisticalInformation(field, op, op2, ft_0, ft_all, rl_0, rl_all, individual_output):
    '''
    process statistics info to calculate improvement rate

    :param field: filed name of interesting statistics info
    :param op: 1 or -1
    :param op2: "sum" or "mean"
    :param ft_0: DataFrame object which contains statistics information (0-hop, fixed signal control)
    :param ft_all: DataFrame object which contains statistics information (0-hop & 1-hop, fixed signal control)
    :param rl_0: DataFrame object which contains statistics information (0-hop, inference-based signal control)
    :param rl_all: DataFrame object which contains statistics information (0-hop & 1-hop, inference-based signal control)
    :param individual_output: processed output
    :return:
    '''
    op_dic = {"sum": np.sum, "mean": np.mean}

    ft_passed = op_dic[op2](ft_0[field])  # np.sum(ft_0[field]) or np.mean(ft_0[field])
    rl_passed = op_dic[op2](rl_0[field])

    if ft_passed == 0.0:
        imp = 0.0
    else:
        imp = op * (rl_passed - ft_passed) / ft_passed * 100
    ft_passed = np.round(ft_passed, 2)
    rl_passed = np.round(rl_passed, 2)
    imp = np.round(imp, 2)

    if DBG_OPTIONS.PrintResultCompare:
        print("0-hop lanes Fixed Time {} {} {} RL {} {} {} Imp {}".format(field, op2, ft_passed,
                                                                          field, op2, rl_passed, imp))
    individual_output = pd.concat(
        [individual_output, pd.DataFrame({'ft_{}_{}_0hop'.format(field, op2): [ft_passed],
                                          'rl_{}_{}_0hop'.format(field, op2): [rl_passed],
                                          'imp_{}_{}_0hop'.format(field, op2): [imp]})], axis=1)

    ft_passed = op_dic[op2](ft_all[field])
    rl_passed = op_dic[op2](rl_all[field])
    ft_passed = np.round(ft_passed, 2)
    rl_passed = np.round(rl_passed, 2)

    if ft_passed == 0.0:
        imp = 0.0
    else:
        imp = op * (rl_passed - ft_passed) / ft_passed * 100
    imp = np.round(imp, 2)

    if DBG_OPTIONS.PrintResultCompare:
        print("1-hop lanes Fixed Time {} {} {} RL {} {} {} Imp {}".format(field, op2, ft_passed,
                                                                          field, op2, rl_passed, imp))
    individual_output = pd.concat(
        [individual_output, pd.DataFrame({'ft_{}_{}_1hop'.format(field, op2, ): [ft_passed],
                                          'rl_{}_{}_1hop'.format(field, op2, ): [rl_passed],
                                          'imp_{}_{}_1hop'.format(field, op2, ): [imp]})], axis=1)
    return individual_output


def __getStatisticsInformationAboutGivenEdgeList(ft_output, rl_output, in_edge_list_0, in_edge_list,
                                                 cut_interval):
    '''
    get statistics information which are related to given edge list

    :param ft_output: DataFrame object which contains statistics information about traffic simulation using fixed signals to control traffic lights
    :param rl_output:  DataFrame object which contains statistics information about traffic simulation using inference to control traffic lights
    :param in_edge_list_0: edge list with 0-hop
    :param in_edge_list: edge list with 0-, 1-hop
    :param cut_interval: last time to delete statistics info
    :return:
    '''
    ft_output2 = ft_output[ft_output['roadID'].str.contains('^' + '$|^'.join(in_edge_list_0) + '$', na=False)]
    rl_output2 = rl_output[rl_output['roadID'].str.contains('^' + '$|^'.join(in_edge_list_0) + '$', na=False)]
    ft_output3 = ft_output[ft_output['roadID'].str.contains('^' + '$|^'.join(in_edge_list) + '$', na=False)]
    rl_output3 = rl_output[rl_output['roadID'].str.contains('^' + '$|^'.join(in_edge_list) + '$', na=False)]
    ft_output2 = ft_output2[ft_output2['intervalbegin'] >= cut_interval]  # 3600 초 이후의 것들만 성능 향상 계산 대상으로 한다.
    rl_output2 = rl_output2[rl_output2['intervalbegin'] >= cut_interval]
    ft_output3 = ft_output3[ft_output3['intervalbegin'] >= cut_interval]
    rl_output3 = rl_output3[rl_output3['intervalbegin'] >= cut_interval]
    return ft_output2, ft_output3, rl_output2, rl_output3


def __compareResultDistInternal(individual_output, comp_tl_list, target_tl_obj, ft_output, rl_output, cut_interval):
        ##-- set the info to be extracted : kind, method
        if 1: ######### compare several factors(avg speed, wt, tt, ... etc)
            ##---- kinds of information to be extracted
            varList = ['VehPassed', 'AverageSpeed', 'WaitingTime', 'AverageDensity', 'SumTravelTime', 'WaitingQLength']

            ##----methods how to calculate
            ##     larger is good if this value is positive, smaller is good if this value is negative
            varOp = [1, 1, -1, -1, -1, -1]
            varOp2 = ['sum', 'mean', 'sum', 'mean', 'sum', 'mean']
        else: ######### compare only average travel time
            ##---- kinds of information to be extracted
            varList = ['VehPassed', 'SumTravelTime']

            ##----methods how to calculate
            ##     larger is good if this value is positive, smaller is good if this value is negative
            varOp = [1, -1]
            varOp2 = ['sum', 'sum']

        in_edge_list = []
        in_edge_list_0 = []

        for tl in comp_tl_list:
            in_edge_list = np.append(in_edge_list, target_tl_obj[tl]['in_edge_list'])
            in_edge_list_0 = np.append(in_edge_list_0, target_tl_obj[tl]['in_edge_list_0'])
            # if DBG_OPTIONS.PrintResultCompare:
            #     print(target_tl_obj[tl]['crossName'], target_tl_obj[tl]['in_edge_list_0'])

        if DBG_OPTIONS.PrintResultCompare:
            # print("\nAll Target TL summary.....")
            print(f"\n{individual_output['name'][0]} summary.....")

        ft_output2, ft_output3, rl_output2, rl_output3 = \
            __getStatisticsInformationAboutGivenEdgeList(ft_output, rl_output, in_edge_list_0, in_edge_list, cut_interval)

        # process by information type(kind) and add it to DataFrame object
        for v in range(len(varList)):
            individual_output = __processStatisticalInformation(varList[v], varOp[v], varOp2[v],
                                                              ft_output2, ft_output3, rl_output2, rl_output3,
                                                              individual_output)
        return individual_output


def compareResultAll(args, target_tl_obj, ft_output, rl_output, model_num, passed_res_comp_skip=-1):
    '''
    compare two result files and calculate improvement rate for each intersection, each SA and overall
    This func compare results and then make statistical info per TL, SA, and whole target.
    originaly from __compareResult() @ SaltConnector.py
    :param args:
    :param target_tl_obj: information about target TL
    :param ft_output: a data frame object which was generated by reading an output (csv) file of simulator
                           that performed the signal control simulation based on the fixed signal
    :param rl_output: a data frame object which was generated by reading an output (csv) file of simulator
                           that performed signal control simulation based on reinforcement learning inference
    :param model_num: number which indicate optimal model which was used to TEST
    :param passed_res_comp_skip : steps to skip to exclude comparison(result comparison)
    :return:
    '''
    ##
    ## Various statistical information related to intersections is extracted from the DataFrame object
    ##      containing the contents of the CSV file created by the simulator.
    ##-- create empty DataFrame object
    total_output = pd.DataFrame()

    if passed_res_comp_skip == -1:
        cut_interval = args.start_time + _RESULT_COMPARE_SKIP_  # 2시간 테스트시 앞에  일정 시간은 비교대상에서 제외
    else:
        cut_interval = args.start_time + passed_res_comp_skip

    if DBG_OPTIONS.PrintResultCompare:
        print(f"training step: {args.start_time} to {args.end_time}")
        print(f"comparing step: {cut_interval} to {args.end_time}")
        print(f"model number: {model_num}")

    #
    # for each intersection
    #
    target_sa_tl_dic = {}  # to save TL info per SA
    for tl in target_tl_obj:
        if "SA " not in target_tl_obj[tl]['signalGroup']:
            target_tl_obj[tl]['signalGroup'] = 'SA ' + target_tl_obj[tl]['signalGroup']
            # add columns : crossName, signalGroup
        individual_output = pd.DataFrame(
            {'name': [target_tl_obj[tl]['crossName']], 'SA': [target_tl_obj[tl]['signalGroup']]})

        individual_output = __compareResultDistInternal(individual_output, [tl], target_tl_obj, ft_output, rl_output,
                                                         cut_interval)
        total_output = pd.concat([total_output, individual_output])

        ## gather SA info
        sa_name = target_tl_obj[tl]['signalGroup']
        if sa_name in target_sa_tl_dic.keys():
            target_sa_tl_dic[sa_name].append(tl)
        else:
            target_sa_tl_dic[sa_name] = [tl]

        if DBG_OPTIONS.PrintResultCompare:
            print(f"sa_name={sa_name}  tl_name={target_tl_obj[tl]['crossName']}  tl_node_id={tl}")

    #
    # for each SA
    #
    for sa in target_sa_tl_dic.keys():
        if DBG_OPTIONS.PrintResultCompare:
            print(f'{sa}')
            for tl in list(target_sa_tl_dic[sa]):
                print(target_tl_obj[tl]['crossName'])

        individual_output = pd.DataFrame({'name': [sa], 'SA': ['total']})
        individual_output = __compareResultDistInternal(individual_output, list(target_sa_tl_dic[sa]), target_tl_obj,
                                                         ft_output, rl_output, cut_interval)
        total_output = pd.concat([total_output, individual_output])

    #
    # for entire target
    #
    individual_output = pd.DataFrame({'name': ['total'], 'SA': ['total']})
    individual_output = __compareResultDistInternal(individual_output, list(target_tl_obj.keys()), target_tl_obj,
                                                     ft_output, rl_output, cut_interval)
    total_output = pd.concat([total_output, individual_output])

    total_output = total_output.sort_values(by=["SA", "name"], ascending=True)

    return total_output


def compareResultTotalOnly(args, target_tl_obj, ft_output, rl_output, model_num, passed_res_comp_skip=-1):
    '''
    compare two result files and calculate improvement rate for each intersection, each SA and overall
    This function compares results of whole target.

    originaly from __compareResult() @ SaltConnector.py
    :param args:
    :param target_tl_obj: information about target TL
    :param ft_output: a data frame object which was generated by reading an output (csv) file of simulator
                           that performed the signal control simulation based on the fixed signal
    :param rl_output: a data frame object which was generated by reading an output (csv) file of simulator
                           that performed signal control simulation based on reinforcement learning inference
    :param model_num: number which indicate optimal model which was used to TEST
    :param passed_res_comp_skip : steps to skip to exclude comparison(result comparison)
    :return:
    '''
    ##
    ## Various statistical information related to intersections is extracted from the DataFrame object
    ##      containing the contents of the CSV file created by the simulator.
    ##-- create empty DataFrame object
    total_output = pd.DataFrame()

    if passed_res_comp_skip == -1:
        cut_interval = args.start_time + _RESULT_COMPARE_SKIP_  # 2시간 테스트시 앞에  일정 시간은 비교대상에서 제외
    else:
        cut_interval = args.start_time + passed_res_comp_skip

    if DBG_OPTIONS.PrintResultCompare:
        print(f"training step: {args.start_time} to {args.end_time}")
        print(f"comparing step: {cut_interval} to {args.end_time}")
        print(f"model number: {model_num}")

    #
    # for entire target
    #
    individual_output = pd.DataFrame({'name': ['total'], 'SA': ['total']})
    individual_output = __compareResultDistInternal(individual_output, list(target_tl_obj.keys()), target_tl_obj,
                                                     ft_output, rl_output, cut_interval)
    total_output = pd.concat([total_output, individual_output])

    total_output = total_output.sort_values(by=["SA", "name"], ascending=True)

    return total_output


##########



def compareResultAndStore(args, env, ft_output, rl_output, problem_var,  comp_skip):
    if args.comp_total_only:
        return compareResultAndStoreTotalOnly(args, env, ft_output, rl_output, problem_var,  comp_skip)
    else:
        return compareResultAndStoreAll(args, env, ft_output, rl_output, problem_var,  comp_skip)

def compareResultAndStoreAll(args, env, ft_output, rl_output, problem_var,  comp_skip):
    '''
    compare result of fxied-time-control and RL-agent-control
    and save the comparison results

    :param args:
    :param env:
    :param ft_output: result of traffic signal control by fixed-time
    :param rl_output: result of traffic signal control by RL-agent
    :param problem_var:
    :param comp_skip: time interval to exclude from result comparison
    :return:
    '''
    result_fn = "{}/output/test/{}_s{}_{}.csv".format(getOutputDirectoryRoot(args), problem_var, comp_skip, args.model_num)
    dst_fn = "{}/{}_s{}.{}.csv".format(args.infer_model_path, _FN_PREFIX_.RESULT_COMP, comp_skip, args.model_num)

    #total_output = compareResult(args, env.tl_obj, ft_output, rl_output, args.model_num, comp_skip)
    total_output = compareResultAll(args, env.tl_obj, ft_output, rl_output, args.model_num, comp_skip)

    total_output.to_csv(result_fn, encoding='utf-8-sig', index=False)

    shutil.copy2(result_fn, dst_fn)

    return result_fn


def compareResultAndStoreTotalOnly(args, env, ft_output, rl_output, problem_var,  comp_skip):
    '''
    compare result of fxied-time-control and RL-agent-control
    and save the comparison results

    :param args:
    :param env:
    :param ft_output: result of traffic signal control by fixed-time
    :param rl_output: result of traffic signal control by RL-agent
    :param problem_var:
    :param comp_skip: time interval to exclude from result comparison
    :return:
    '''
    result_fn = "{}/output/test/{}_s{}_{}.csv".format(getOutputDirectoryRoot(args), problem_var, comp_skip, args.model_num)
    dst_fn = "{}/{}_s{}.{}.csv".format(args.infer_model_path, _FN_PREFIX_.RESULT_COMP, comp_skip, args.model_num)

    #total_output = compareResult(args, env.tl_obj, ft_output, rl_output, args.model_num, comp_skip)
    total_output = compareResultTotalOnly(args, env.tl_obj, ft_output, rl_output, args.model_num, comp_skip)

    total_output.to_csv(result_fn, encoding='utf-8-sig', index=False)

    shutil.copy2(result_fn, dst_fn)

    return result_fn


##
##
## from run_off_ppo import Env as EnvSingle
## class Env(EnvSingle) ....
##
## Env, isolated, IsolatedEnv, .... 등 새로 작성 : EnvDist, isolatedDist, IsolatedEnvDist, ...
##   ---> 검토 후 수정된 부분을 Env, ...등에  반영 요청 

class AgentDist(Agent):
    
    def __init__(self, env_name, agent_num, action_sizes, state_sizes, sa_name_list, target_sa_name_list,  ppo_config, problem_var, args):
        self._init_holder(agent_num, action_sizes)
        self.env_name = env_name
        self.ppo_config = ppo_config
        self.problem_var = problem_var
        self.args = args

        if 1: # added for dist; to set the value of 'agent.is_train' correctly
            self.num_of_agent = agent_num
            self.sa_name_list = sa_name_list
            self.target_sa_name_list = target_sa_name_list
        
        self.ppo_agent = []
        for i in range(agent_num):
            if 1: # added for dist; set the value of 'agent.is_train' correctly
                sa_name = self.sa_name_list[i]
                is_train_target = sa_name in self.target_sa_name_list
                ppo_config["is_train"] = is_train_target

            agent = PPOAgentTF2(env_name, ppo_config, action_sizes[i], state_sizes[i], sa_name.strip().replace(' ', '_'))
            # if 1:
            #     print(f"DELETE sa_name = {sa_name}  action_sizes[{i}] = {action_sizes[i]} in AgentDist")
            self.ppo_agent.append(agent)

        if 1: # added for dist
            self.__loadModelAndReplayMemory()


    def __loadModelAndReplayMemory(self):
        for i in range(self.num_of_agent):
            sa_name = self.sa_name_list[i]
            an_agent = self.ppo_agent[i]
            is_train_target = an_agent.is_train

            #-- for trained model
            ##--   load trained model
            fn_prefix = makeLoadModelFnPrefix(self.args, self.problem_var, is_train_target)

            if len(fn_prefix) > 0:
                if DBG_OPTIONS.PrintTrain:
                    waitForDebug(f"agent for {sa_name} will load model parameters from {fn_prefix}")
                else:
                    print(f"agent for {sa_name} will load model parameters from {fn_prefix}")
                an_agent.loadModel(fn_prefix)


            else:
                if DBG_OPTIONS.PrintTrain:
                    waitForDebug(
                        f"agent for {sa_name} will training without loading a pre-trained model parameter")
                else:
                    print(f"agent for {sa_name} will training without loading a pre-trained model parameter")


            # --   for CumulateReplayMemory
            ##--     load stored replay memory if is_train_target and args.cumulative_training and (args.infer_model_num >=0)
            if is_train_target and self.args.cumulative_training and (int(self.args.infer_model_num) >= 0):
                fn_replay_memory_object = "{}/model/{}/{}_{}.dmp".format(getOutputDirectoryRoot(self.args), self.args.method,
                                                                         _FN_PREFIX_.REPLAY_MEMORY, an_agent.id)

                an_agent.loadReplayMemory(fn_replay_memory_object)
                if DBG_OPTIONS.PrintTrain:
                    print(f"### loaded len(replay memory for {an_agent.id})={len(an_agent.memory.states)}")


    def store(self, current_state, action, reward, new_state, done, logp, info):

        idx_of_act_sa = info['idx_of_act_sa']
        for i in idx_of_act_sa:
            if self.ppo_agent[i].is_train:
                if current_state[i] is not None and new_state[i] is not None:
                    self.ppo_agent[i].memory.store(current_state[i],
                                           action[i],
                                           reward[i],
                                           new_state[i],
                                           done,
                                           logp[i])


    def train(self):
        for agent in self.ppo_agent:
            if agent.is_train:
                # print(f"DELETE agent for {agent.id} is now replay : memory.getSize()={agent.memory.getSize()}")
                agent.replay()


    def save_agent(self, trial):
        args = self.args
        problem_var = self.problem_var
        fn_prefix = "{}/model/{}/{}-{}-trial_{}".format(getOutputDirectoryRoot(args), args.method, args.method.upper(),
                                                        problem_var, trial)
        for agent in self.ppo_agent:
            if agent.is_train:
                agent.saveModel(fn_prefix)

    def load_agent(self, trial):
        args = self.args
        problem_var = self.problem_var
        fn_prefix = "{}/model/{}/{}-{}-trial_{}".format(getOutputDirectoryRoot(args), args.method, args.method.upper(),
                                                        problem_var, trial)
        for agent in self.ppo_agent:
            agent.loadModel(fn_prefix)

#----------------------------------------------------


class EnvDist(Env):

    def __init__(self, args, output_dir_prefix):
        self.output_dir_prefix = output_dir_prefix
        super(EnvDist, self).__init__(args)


    ## added _calculateStepSize() func to fix bug in state-augmentation
    def _calculateStepSize(self, trial_len, sa_cycle):
        return int(trial_len / (sa_cycle * args.control_cycle))+1


    def simulationStart(self):
        libsalt.start(self.salt_scenario, self.output_dir_prefix)


    def get_agent_configuration(self):
        return self.get_agent_configurationV2()


    def get_agent_configurationV1(self):
        env_name, agent_num, action_sizes, state_sizes, ppo_config, problem_var, sa_name_list, args = super(EnvDist, self).get_agent_configuration()
        return env_name, agent_num, action_sizes, state_sizes, ppo_config, problem_var, sa_name_list, args, self.target_sa_name_list

    
    def get_agent_configurationV2(self): #care all actions
        action_sizes = []
        state_sizes = []
        
        for i in range(self.agent_num):
            sa_name = self.sa_name_list[i]

            state_space = self.sa_obj[sa_name]['state_space']
            action_space = self.sa_obj[sa_name]['action_space']

            # if 1:
            #     print(f"DELETE sa_name = {sa_name}  action_space = {action_space} in EnvDist")

            ##-- TF 2.x : ppo_continuous_hs,py
            action_size = action_space.shape[0]
            #print('action_size', action_size)
            #state_size = (state_space,)
            state_size = (state_space+self.step_size[i],)

            ##-- @todo check & care all action, directly use SaltSappoEnv
            if 0:
                if self.args.action in ['gt', 'ft']:
                    state_size = (state_space+self.step_size[i],)
                elif self.args.action in ['gr', 'offset', 'gro']:
                    state_size = (state_space,)
                else:
                    raise Exception(f"Internal Error: You should check whether {self.args.action} is valid action")

            if not DBG_OPTIONS.DoStateAugmentation:
                state_size = (state_space,)
            else:
                state_size = (state_space+self.step_size[i],)

            action_sizes.append(action_size)
            state_sizes.append(state_size)
            
        return self.env_name, self.agent_num, action_sizes, state_sizes, self.sa_name_list, self.target_sa_name_list


    def _reshape_state(self, state, idx_of_act_sa):

        state = copy.deepcopy(state)

        for i in idx_of_act_sa:

            obs = state[i]

            if not DBG_OPTIONS.DoStateAugmentation:
                pass
            else:
                #print(f"### Env::_reshape() {i} before augment() ... state[{i}].shape={obs.shape}")
                obs = self.state_augment[i].augment(obs)
                #print(f"### Env::_reshape() {i} after augment() ... state[{i}].shape={obs.shape}")


            obs = obs.reshape(1, -1)  # [1,2,3]  ==> [ [1,2,3] ]
            state[i] = obs

        return state

def isolatedDist(conn, args, output_dir_prefix):
    
    #env = Env(args)
    env = EnvDist(args, output_dir_prefix)
    pid = os.getpid()
    print(f"[{pid}]  isolatedDist() process for {args.target_TL} is created")

    while True:
        msg = conn.recv()

        
        if msg[0] == 'get_agent_configuration':
            config = env.get_agent_configuration()
            conn.send(config)
            
        elif msg[0] == 'reset':
            state, info = env.reset()       
            conn.send((state, info))
            
        elif msg[0] == 'step':
            state, reward, done, info = env.step(msg[1])

            transition = (state, reward, done, info)
            conn.send(transition)
            
        elif msg[0] == 'close':
            conn.close()
            env.close()
            del env
            print(f"[{pid}]  msg 'close' processed in isolatedDist() process for {args.target_TL}")
            break


       
class IsolatedEnvDist(IsolatedEnv):

    def __init__(self, args, env_name_postfix, output_dir_prefix,  max_run=100):
        self.output_dir_prefix = output_dir_prefix
        super(IsolatedEnvDist, self).__init__(args, max_run)

        ppo_config, problem_var = makeConfigAndProblemVar(args)
        env_name, agent_num, action_sizes, state_sizes, sa_name_list, target_sa_name_list = self.__get_agent_configuration()

        self.env_name = f"{env_name}_{env_name_postfix}"
        self.agent_num = agent_num
        self.action_sizes = action_sizes
        self.state_sizes = state_sizes
        self.sa_name_list = sa_name_list
        self.target_sa_name_list = target_sa_name_list
        self.ppo_config = ppo_config
        self.problem_var = problem_var

    def _create_env_process(self):

        if self._env_process is not None: self.close()

        parent_conn, child_conn = Pipe()
        self._conn = parent_conn
        self._env_process = Process(target=isolatedDist, args=(child_conn, self.args, self.output_dir_prefix))
        # self._env_process.daemon = True
        self._env_process.start()

    def __get_agent_configuration(self):

        self._conn.send(('get_agent_configuration',))
        config = self._conn.recv()
        return config

    def get_agent_configuration(self):
        return self.env_name, self.agent_num, self.action_sizes, self.state_sizes, \
               self.sa_name_list, self.target_sa_name_list, self.ppo_config, self.problem_var, self.args



# def run_train_episode(trial, env, agent)
#
# def run_test_episode(trial, env, agent)
#

# def run_valid_episode(trial, env, agent, best_trial, best_score)
def run_valid_episode_dist(trial, env, agent, best_trial, best_score):
    info = run_test_episode(trial, env, agent)    
    score = info['episodic_reward']
    if score > best_score:
        best_trial = trial
        best_score = score
    
    #print('Best trial:', best_trial, best_score)
    #return best_trial, best_score
    return best_trial, best_score, info ## add info as a return value

#
# def run_multi_thread(trial, envs, agent)



        
#----------------------------------------------------


##########

def dumpOptimalModelInfo(args, ma1_reward_list, instantaneous_opt_model_num=-1):
    '''
    dump optimal model info
    :param args: passed commandline arguments
    :param ma1_reward_list: reward info per epoch
    :param instantaneous_opt_model_num: optimal model number(instantaneous)
    '''
    num_of_candidate = args.num_of_optimal_model_candidate  # default 3
    model_save_period = args.model_save_period  # default 1

    _, problem_var = makeConfigAndProblemVar(args)

    ##-- get the trial number that gave the best performance
    if instantaneous_opt_model_num != -1:
        optimal_model_num = instantaneous_opt_model_num
    else:
        if args.epoch == 1:
            optimal_model_num = 0
        else:
            optimal_model_num = findOptimalModelNum(ma1_reward_list, model_save_period, num_of_candidate)

    ##-- make the prefix of file name which stores trained model
    fn_optimal_model_prefix = "{}/model/{}/{}-{}-trial". \
        format(getOutputDirectoryRoot(args), args.method, args.method.upper(), problem_var)

    ##-- make the file name which stores trained model that gave the best performance
    fn_optimal_model = "{}-{}".format(fn_optimal_model_prefix, optimal_model_num)

    if DBG_OPTIONS.PrintFindOptimalModel:
        waitForDebug("run.py : return dumpOptimalModelInfo() : fn_opt_model = {}".format(fn_optimal_model))

    fn_opt_model_info = '{}.{}'.format(_FN_PREFIX_.OPT_MODEL_INFO, convertSaNameToId(args.target_TL.split(",")[0]))

    if int(args.infer_model_num) < 0:
        writeLine(fn_opt_model_info, fn_optimal_model)
    else:
        appendLine(fn_opt_model_info, fn_optimal_model)

    return fn_optimal_model




def dumpReplayMemory(args, agents):
    '''
    dump replay memory
    :param args: passed commandline arguments
    :param agents: Agent object which holds all agents
    '''
    ##-- for CumulateReplayMemory
    if args.cumulative_training:
        for i in range(agents.num_of_agent):
            if not agents.ppo_agent[i].is_train:  # if it is not the target of training
                continue

            fn_replay_memory_object = "{}/model/{}/{}_{}.dmp".format(getOutputDirectoryRoot(args), args.method,
                                                                     _FN_PREFIX_.REPLAY_MEMORY, agents.ppo_agent[i].id)

            agents.ppo_agent[i].dumpReplayMemory(fn_replay_memory_object)
            # print(f"### dumped len(replay memory for  {ppo_agent[i].id})={len(ppo_agent[i].memory.states)}")
    else:
        print(f"args.cumulative-training is {args.cumulative_training}")





def trainSappo(args):
    '''
    model train
      - this is work well with multiple SA
      - infer-TL is considered
    :param args:
    :return:
    '''


    if 1:
        # calculate the length of simulation step using argument and scenario file
        start_time, end_time = getSimulationStartStepAndEndStep(args)
        trial_len = end_time - start_time

        # set start_/end_time which will be used to train
        args.start_time = start_time
        args.end_time = end_time

    
    valid_args = copy.deepcopy(args)
    #valid_args.scenario = '12th'
    
    num_envs = args.num_concurrent_env #10
    envs =  []

    #train_output_dir_prefix = f"./output/{args.mode}"
    train_output_dir_prefix = args.output_home

    for i in range(num_envs):
        #env = IsolatedEnv(args)
        env = IsolatedEnvDist(args, f"train_env_{i}", f"{train_output_dir_prefix}_{i}",  args.max_run_with_an_env_process)
        envs.append(env)
    
    #create a validation/test evnironment. 
    valid_output_dir_prefix = valid_args.output_home
    valid_env = IsolatedEnvDist(valid_args, "valid_env", valid_output_dir_prefix, valid_args.max_run_with_an_env_process)
    best_trial = 0; best_score = -np.inf
    
    print('Train:', args.scenario_file_path, 'Valid:', valid_args.scenario_file_path)
    
    #agent_config = envs[0].get_agent_configuration()
    agent_config = valid_env.get_agent_configuration()

    print(f"agent_config={agent_config}")
    agent = AgentDist(*agent_config)

    if int(args.infer_model_num) < 0:
        # fill the replay memory with random plays
        while agent.getMemorySize() < args.mem_len: 
            run_multi_thread(0, envs, agent)

        # if 1:
        #     for an_agent in agent.ppo_agent:
        #         if an_agent.is_train:
        #             print(f"DELETE agent for {an_agent.id} : fill the replay memory : memory.getSize()={an_agent.memory.getSize()}")

    else:
        # we already load replay memory within AgentDist::__init__() 
        #   if it is cummulative_training & training target
        pass 


    # @todo 최적 모델 선정 방법: dbg_options_opt_choice
    #       choice 1 : best score
    #       choice 2 : training reward w/ exploration
    #       choice 3 : training reward w/o exploration
    dbg_options_opt_choice = 1
    ep_reward_list = []

    for trial in range(args.epoch):
        #run_multi_thread(trial, envs, agent)
        start_time = time.time()
        rewards, mean, std = run_multi_thread(trial, envs, agent)
        end_time = time.time()
        print(f"Training time for {args.target_TL}: {end_time - start_time} seconds")

        if dbg_options_opt_choice == 2 :
            ### rewards에는num_envs 개수만큼 들어있다. 이것(평균)을 계속 저장해서 가지고 있다가 최적 모델 선정에 활용할 수 있을 것 같다.
            ## gather reward info of trials
            ep_reward_list.append(np.average(rewards))

        print("##### now..... agent train for parameter update")
        start_time = time.time()
        agent.train()
        end_time = time.time()
        print(f"Agent Training(Update) time for {args.target_TL}: {end_time - start_time} seconds")

        ### model save
        if trial % args.model_save_period == 0:
            agent.save_agent(trial)
            
            if dbg_options_opt_choice != 3:
                best_trial, best_score = run_valid_episode(trial, valid_env, agent, best_trial,  best_score)
            else:
                #@todo info에 보상 정보가 들어 있다. 이를 계속 저장 후에 최적 모델 선정에 활용할 수 있을 것 같다.
                best_trial, best_score, info = run_valid_episode_dist(trial, valid_env, agent, best_trial,  best_score)
                # model_save_period에 한번씩 보상이 측정된 보상 값을 model_save_period회 저장한다... 
                #      dumpOptimalModelInfo()에서 최적 모델 선정 알고리즘을 그대로 이용하기 위함.
                ep_reward_list = ep_reward_list + [info['episodic_reward']]*args.model_save_period

        #print('Best trial:', best_trial, best_score)


    for i, env in enumerate(envs):
        env.close()
        print(f"pid={os.getpid()} {env.env_name} : {i}-th env.close() called ")

    valid_env.close()
    print(f"pid={os.getpid()} valid_env.close() called ")

    if dbg_options_opt_choice != 1:
        fn_optimal_model = dumpOptimalModelInfo(args, ep_reward_list)
    else :
        fn_optimal_model = dumpOptimalModelInfo(args, ep_reward_list, best_trial)

    ## dump replay memory
    dumpReplayMemory(args, agent)

    return fn_optimal_model
 



def printImprovementRate(result_fn, target_sa_name_list=[], msg="Skip one hour"):
    df = pd.read_csv(result_fn, index_col=0)
    for sa in target_sa_name_list:
        printImprovementRateInternal(df, sa, msg)
    tot_imp_rate = printImprovementRateInternal(df, 'total', msg)
    return tot_imp_rate


# def calculateImprovementRate(df, target):  # TSOUtil.py
#     ft_passed_num = df.at[target, 'ft_VehPassed_sum_0hop']
#     rl_passed_num = df.at[target, 'rl_VehPassed_sum_0hop']
#     ft_sum_travel_time = df.at[target, 'ft_SumTravelTime_sum_0hop']
#     rl_sum_travel_time = df.at[target, 'rl_SumTravelTime_sum_0hop']
#
#     ft_avg_travel_time = ft_sum_travel_time / ft_passed_num
#     rl_avg_travel_time = rl_sum_travel_time / rl_passed_num
#     imp_rate = (ft_avg_travel_time - rl_avg_travel_time) / ft_avg_travel_time * 100
#     return imp_rate

def printImprovementRateInternal(df, target, msg="Skip one hour"):
    imp_rate = calculateImprovementRate(df, target)
    print(f'{msg} Average Travel Time ({target}): {imp_rate}% improved')
    return imp_rate

def testSappo(args):
    '''
    test trained model

    :param args:
    :return:
    '''

    env = EnvDist(args, args.output_home)
    # _env_name, _agent_num, _action_sizes, _state_sizes, sa_name_list, target_sa_name_list = env.get_agent_configuration()
    cfg1 = env.get_agent_configuration()

    ppo_config, problem_var = makeConfigAndProblemVar(args)
    agent_config = cfg1 + (ppo_config, problem_var, args)
    agent = AgentDist(*agent_config)
    # if args.action != 'fx': agent.load_agent(trial=args.model_num)

    start_time = time.time()
    run_test_episode(0, env, agent)
    end_time = time.time()
    print(f"Time used to run with test-mode : {end_time - start_time} seconds")


    # _env_name, _agent_num, _action_sizes, _state_sizes, sa_name_list, target_sa_name_list, ppo_config, problem_var, args
    problem_var = agent_config[7] # i.e, problem_var
    # compare traffic simulation results
    if args.result_comp:
        print("Now.... doing result comparison...")

        start_time = time.time()

        ft_output = pd.read_csv("{}/output/simulate/{}".format(getOutputDirectoryRoot(args), _RESULT_COMP_.SIMULATION_OUTPUT))
        rl_output = pd.read_csv("{}/output/test/{}".format(getOutputDirectoryRoot(args), _RESULT_COMP_.SIMULATION_OUTPUT))

        comp_skip = _RESULT_COMPARE_SKIP_
        result_fn = compareResultAndStore(args, env, ft_output, rl_output, problem_var, comp_skip)
        if args.comp_total_only:
            printImprovementRate(result_fn, msg=f'Skip {comp_skip} second')
        else:
            printImprovementRate(result_fn, env.target_sa_name_list, msg=f'Skip {comp_skip} second')


        if DBG_OPTIONS.ResultCompareSkipWarmUp: # comparison excluding warm-up time
            comp_skip = args.warmup_time
            result_fn = compareResultAndStore(args, env, ft_output, rl_output, problem_var, comp_skip)
            if args.comp_total_only:
                printImprovementRate(result_fn, msg=f'Skip {comp_skip} second')
            else:
                printImprovementRate(result_fn, env.target_sa_name_list, msg=f'Skip {comp_skip} second')

        end_time = time.time()
        print(f"Time used to compare result : {end_time - start_time} seconds")

    avg_reward = 0
    return avg_reward




def fixedTimeSimulate(args):
    '''
    do traffic control with fixed signal
    :param args:
    :return:
    '''

    # calculate the length of simulation step of this trial : trial_len
    start_time, end_time = getSimulationStartStepAndEndStep(args)
    trial_len = end_time - start_time

    # set start_/end_time which will be used to simulate
    args.start_time = start_time
    args.end_time = end_time


    if args.copy_scenario_file:
        salt_scenario = copyScenarioFiles(args.scenario_file_path)
    else:
        salt_scenario = args.scenario_file_path

    possible_sa_name_list = makePosssibleSaNameList(args.target_TL)

    target_tl_obj, target_sa_obj, _ = getSaRelatedInfo(args, possible_sa_name_list, salt_scenario)
    target_sa_name_list = list(target_sa_obj.keys())
    target_tl_id_list = list(target_tl_obj.keys())


    ### 가시화 서버용 교차로별 고정 시간 신호 기록용
    output_ft_dir = f'{getOutputDirectoryRoot(args)}/output/{args.mode}'
    #output_ft_dir = f'{args.io_home}/output/{args.mode}/{args.scenario}'
    #fn_ft_phase_reward_output = f"{output_ft_dir}/ft_phase_reward_output.txt"
    fn_ft_phase_reward_output = f"{output_ft_dir}/{_RESULT_COMP_.FT_PHASE_REWARD_OUTPUT}"

    writeLine(fn_ft_phase_reward_output, 'step,tl_name,actions,phase,reward,avg_speed,avg_travel_time,sum_passed,sum_travel_time')

    reward_mgmt = SaltRewardMgmtV3(args.reward_func, args.reward_gather_unit, args.action_t,
                                       args.reward_info_collection_cycle, target_sa_obj, target_tl_obj,
                                       target_sa_name_list, len(target_sa_name_list))


    ### 교차로별 고정 시간 신호 기록하면서 시뮬레이션
    libsalt.start(salt_scenario, args.output_home)
    libsalt.setCurrentStep(start_time)

    actions = []

    sim_step = libsalt.getCurrentStep()

    tso_output_info_dic = initTsoOutputInfo()

    for tlid in target_tl_id_list:
        avg_speed, avg_tt, sum_passed, sum_travel_time = gatherTsoOutputInfo(tlid, target_tl_obj, num_hop=0)

        #todo should consider the possibility that TOD can be changed
        offset = target_tl_obj[tlid]['offset']
        duration = target_tl_obj[tlid]['duration']

        if DBG_OPTIONS.PrintAction:
            cross_name = target_tl_obj[tlid]['crossName']
            green_idx = target_tl_obj[tlid]['green_idx']
            print(f'cross_name={cross_name} offset={offset} duration={duration} green_idx={green_idx}  green_idx[0]={green_idx[0]}')
                  # cross_name=진터네거리 offset=144 duration=[18, 4, 72, 4, 18, 4, 28, 4, 25, 3] green_idx=(array([0, 2, 4, 6, 8]),)  green_idx[0]=[0 2 4 6 8]

        appendTsoOutputInfoSignal(tso_output_info_dic, offset, duration)
        tso_output_info_dic = appendTsoOutputInfo(tso_output_info_dic, avg_speed, avg_tt, sum_passed, sum_travel_time)

    for i in range(trial_len):
        libsalt.simulationStep()
        sim_step += 1

        # todo 일정 주기로 보상 값을 얻어와서 기록한다.
        appendPhaseRewards(fn_ft_phase_reward_output, sim_step, actions, reward_mgmt,
                               target_sa_obj, target_sa_name_list, target_tl_obj, target_tl_id_list,
                               tso_output_info_dic)


    print("{}... ft_step {}".format(fixedTimeSimulate.__name__, libsalt.getCurrentStep()))

    for k in tso_output_info_dic:
        tso_output_info_dic[k].clear()
    del tso_output_info_dic

    libsalt.close()



if __name__ == "__main__":
    ## dump launched time
    launched = datetime.datetime.now()
    print(f'TSO(pid={os.getpid()}) launched at {launched}')

    DBG_OPTIONS.YJLEE = False
    args = parseArgument()

    # if args.map=="dj200":
    #     args.target_TL = "SA 3, SA 28, SA 101, SA 6, SA 41, SA 20, SA 37, SA 38, SA 9, SA 1, "
    #                      + "SA 57, SA 102, SA 104, SA 98, SA 8, SA 33, SA 59, SA 30"

    args.target_TL = removeWhitespaceBtnComma(args.target_TL)
    args.infer_TL = removeWhitespaceBtnComma(args.infer_TL)

    # getOutputDirectoryRoot() returns  f"{args.io_home}/{args.output_home}"
    dir_name_list = [
                         f"{getOutputDirectoryRoot(args)}/model",
                         f"{getOutputDirectoryRoot(args)}/model/{args.method}",
                         f"{getOutputDirectoryRoot(args)}/logs",
                         f"{getOutputDirectoryRoot(args)}/output",
                         f"{getOutputDirectoryRoot(args)}/output/simulate",
                         f"{getOutputDirectoryRoot(args)}/output/test",
                         f"{getOutputDirectoryRoot(args)}/output/train",
                         f"{args.io_home}/data/envs/salt/data",
        ]

    # check traffic environment : lib path
    checkTrafficEnvironment(args.traffic_env)

    makeDirectories(dir_name_list)

    if args.mode == 'train':
        if args.method == 'sappo':
            trainSappo(args)
            #trainSappoWithMultiEnv(args)
        else:
            print("internal error : {} is not supported".format(args.method))

    elif args.mode == 'test':
        if args.method == 'sappo':
            testSappo(args)
        else:
            print("internal error : {} is not supported".format(args.method))

    elif args.mode == 'simulate':
        fixedTimeSimulate(args)

    ## dump terminated time
    terminated = datetime.datetime.now()
    print(f'terminated at {terminated}')

    ## calculate & dump duration
    interval = terminated-launched
    print(f'Time taken for experiment was {interval} ')  #  2 days, 14:36:28.245176
