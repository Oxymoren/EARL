import gym
import numpy as np
from earl.model import EvoACModel
import torch
from earl.storage import EvoACStorage
from earl.ea import EA
from earl.logger import EvoACLogger
import scipy.special as sps

class EvoACRunner(object):
    """
    This class is the primary coordinator of the program.
    Data passing and the primary control loops are in here.

    Params:
        config: (dict): the experiment configuration
    """
    def __init__(self, config):
        self.config = config
        self.config_evo = config['earl']
        self.config_net = config['neural_net']
        self.config_exp = config['experiment']

        if self.config_exp['env'] == "CartPole-v1":
            self.stop_fit = 475.0
        elif self.config_exp['env'] == "LunarLander-v2":
            self.stop_fit = 200.0

        self._set_device()

        self.env = gym.make(self.config_exp['env'])
        self.test_env = gym.make(self.config_exp['env'])
        self.logger = EvoACLogger(config)


    def train(self):
        """
        The primary loop of the program. Runs the whole experiment.
        """
        for run_idx in range(self.config_exp['num_runs']):
            self._reset_experiment()
            self.timesteps = 0
            self.stop_counter = 0
            self.last_log = -9999999

            for self.gen_idx in range(10000):
                self.storage.reset_storage()
                for pop_idx in range(self.config_evo['pop_size']):
                    self._run_episode(pop_idx)

                self._update_evo_ac()

                if self.timesteps - self.last_log >= self.config_exp['log_interval'] or self.timesteps > self.config_exp['timesteps']:
                    test_fit = self._test_algorithm()

                    self.logger.save_fitnesses(self.model, test_fit, self.storage.fitnesses, self.policy_loss_log,
                                                self.value_loss_log, self.gen_idx, self.timesteps)

                    if self.gen_idx % self.config_exp['print_interval'] == 0:
                        self.logger.print_data()

                    self.last_log = self.timesteps

                    if test_fit >= self.stop_fit:
                        break

                self.model.insert_params(self.new_pop)

                if self.timesteps > self.config_exp['timesteps']:
                    break

            self.logger.end_run()
        self.logger.end_experiment()

    def _run_episode(self, pop_idx):
        """
        Runs a training episode on the specified population member.

        Params:
            pop_idx: the index of the population member
        """
        obs = self.env.reset()
        fitness = 0

        while True:

            action, log_p_a, entropy, value = self.model.get_action(self.storage.obs2tensor(obs).to(self.device), pop_idx)

            self.timesteps += 1

            obs, reward, done, info = self.env.step(action.cpu().numpy())
            fitness += reward

            self.storage.insert(pop_idx, reward, action, log_p_a, value, entropy)

            if done:
                break

        self.storage.insert_fitness(pop_idx, fitness)


    def _update_evo_ac(self):
        """
        Performs a full parameter update based on the previous accumulated
        experiences in the stoarage module.
        """
        self.model.opt.zero_grad()
        loss, self.policy_loss_log, self.value_loss_log = self.storage.get_loss()
        loss.backward()
        self.evo.set_grads(self.model.extract_grads())

        self.model.opt.step()

        self.evo.set_fitnesses(self.storage.fitnesses)

        with torch.no_grad():
            self.new_pop = self.evo.create_new_pop()

    def _reset_experiment(self):
        """
        Sets up the experiment for a new run. Rests env, storage, model.
        """
        obs_size = np.prod(np.shape(self.env.observation_space))
        num_pop = self.config_evo['pop_size']
        max_ep_steps = self.env._max_episode_steps
        value_coeff = self.config_evo['value_coeff']
        entropy_coff = self.config_evo['entropy_coeff']

        print("NEW RUN")

        self.storage = EvoACStorage(num_pop, self.config, self.device)
        self.model = EvoACModel(self.config, self.device).to(self.device)
        self.evo = EA(self.config)
        self.evo.set_params(self.model.extract_params())

    def _test_algorithm(self):
        """
        Runs a test set of 100 rollouts on the current model.
        No data is stored for learning. At test time, the actions are
        ensembled together.

        Returns: the mean score/fitness of the 100 runs
        """
        with torch.no_grad():
            fitnesses = []

            for _ in range(100):
                fitness = 0
                obs = self.test_env.reset()
                while True:
                    action = self._get_test_action(obs)
                    obs, rewards, done, info = self.test_env.step(action)
                    fitness += rewards
                    if done:
                        break
                fitnesses.append(fitness)
            return np.mean(fitnesses)

    def _get_test_action(self, obs):
        """
        Performs the ensemble (as defined in configuration).
        Used in testing.

        Params:
            obs: (np array): the environment observation

        Returns: action (int): the action to take
        """
        obs = self.storage.obs2tensor(obs).to(self.device)
        fitnesses = self.storage.fitnesses
        if self.config_exp['test_strat'] == 'best':
            best_pop = np.argmax(fitnesses)
            action, _, _, _ = self.model.get_action(obs, best_pop)
            action = action.cpu().numpy()
        elif self.config_exp['test_strat'] == 'softmax':
            probs = sps.softmax(fitnesses)
            pop_idx = np.random.choice(self.config_evo['pop_size'], 1, p=probs)
            action, _, _, _ = self.model.get_action(obs, pop_idx[0])
            action = action.cpu().numpy()
        elif self.config_exp['test_strat'] == 'weightedvote':
            actions = [self.model.get_action(obs, pop_idx)[0].item() for pop_idx in range(self.config_evo['pop_size'])]
            action_votes = [0] * self.test_env.action_space.n
            for mod_action, weight in zip(actions, fitnesses):
                action_votes[mod_action] += weight
            action = np.argmax(action_votes)
        return action

    def _set_device(self):
        if not torch.cuda.is_available() or ('force_cpu' in self.config_exp and self.config_exp['force_cpu']):
            self.device = torch.device('cpu')
            print("Running on CPU")
        else:
            self.device = torch.device('cuda:0')
            print("Running on GPU")
