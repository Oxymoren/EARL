import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.distributions import Categorical


class EvoACModel(nn.Module):
    """
    The model, both actor and critic, in one class.
    Implemented as a torch Module

    Params:
        config_dict (dict): the whole experiment configuration


    TODO: Add ability to contain lstm and RNN layers.
    """
    def __init__(self, config_dict, device):
        super().__init__()
        self.device = device
        self.evo_config = config_dict['earl']
        self.net_config = config_dict['neural_net']

        self._init_model_layers()
        self.opt = optim.Adam(self.parameters(), lr=self.net_config['lr'])


    def _init_model_layers(self):
        """
        Creates the various layers from the config.
        See _add_layers for more detail.
        """
        self.shared_net = self._add_layers(self.net_config['shared'])
        self.policy_nets = [self._add_layers(self.net_config['policy'])
                            for _ in range(self.evo_config['pop_size'])]
        self.value_net = self._add_layers(self.net_config['value'])


    def _add_layers(self, layer_config):
        """
        Takes a list of layers (see a config) and
        calls and parameterizes the corresponding torch.nn class.
        See _add_layer() for more details.

        Outputs the list of layers as a ModuleList
        """
        output_ml = nn.ModuleList()
        for layer in layer_config:
            output_ml.append(self._add_layer(
                layer['type'],
                layer['params'],
                layer['kwargs'])).to(self.device)
        return output_ml


    def extract_params(self):
        """
        Loops through the policy nets and extracts the parameters from the model.

        Returns: (list): Indexed as [individual][layer][parameter]
        """
        with torch.no_grad():
            extracted_parameters = []
            for individual in self.policy_nets:
                layer_params = []
                for layer in individual:
                    for name, parameter in layer.named_parameters():
                        layer_params.append(parameter.detach())
                extracted_parameters.append(layer_params)
            return extracted_parameters


    def insert_params(self, incoming_params):
        """
        Loops through the policy nets and inserts the parameters into the model.

        Params:
            incoming_params: (list): Indexed as [individual][layer][parameter]
        """
        with torch.no_grad():
            for pop_idx in range(len(self.policy_nets)):
                params_idx = 0
                individual = self.policy_nets[pop_idx]
                for layer in individual:
                    state_dict = layer.state_dict()
                    for name, parameter in layer.named_parameters():
                        state_dict[name] = incoming_params[pop_idx][params_idx]
                        params_idx += 1
                    layer.load_state_dict(state_dict)


    def extract_grads(self):
        """
        Loops through the policy nets and extracts the gradients from the model.

        Returns: (list): Indexed as [individual][layer][parameter]
        """
        with torch.no_grad():
            extracted_grads = []
            for individual in self.policy_nets:
                layer_grads = []
                for layer in individual:
                    for name, parameter in layer.named_parameters():
                        layer_grads.append(parameter.grad.detach())
                extracted_grads.append(layer_grads)
            return extracted_grads


    def forward(self, x, pop_idx):
        """
        Standard way of calling a pytorch module. The only excpetion is that the
        pop_idx needs to be suppiled.

        Params:
            x (tensor): the input state
            pop_idx (int): the index of the population member to use.

        Returns: policy, value
            policy: (tensor) the list of probabilities of the action space
            value: (tensor) the caluclated value estimation of the state
        """
        shared = x
        for layer in self.shared_net:
            shared = layer(shared)

        policy = shared
        value = shared

        for layer in self.policy_nets[pop_idx]:
            policy = layer(policy)

        for layer in self.value_net:
            value = layer(value)


        # return values for both actor and critic as a tuple of 2 values:
        # 1. a list with the probability of each action over the action space
        # 2. the value from state s_t
        return policy, value


    def get_action(self, state, pop_idx):
        """
        Standard way of calling a pytorch module. The only excpetion is that the
        pop_idx needs to be suppiled.

        Params:
            state (tensor): the input state
            pop_idx (int): the index of the population member to use.

        Returns: policy, log_prob, value
            action (tensor): the action to take
            log_prob (tensor): the log probability of that action being taken
            entropy (tensor): the entropy of the action probability
            value (tensor): the estimated value of the current state

        """
        policy, value = self(state, pop_idx)

        action_prob = F.softmax(policy, dim=-1)
        cat = Categorical(action_prob)
        action = cat.sample()

        return action, cat.log_prob(action), cat.entropy().mean(), value


    def _add_layer(self, layer_type, layer_params, layer_kwargs):
        """
        Finds the relevant pytorch function and calls it with params and kwargs

        Pytorch.nn Classes: https://pytorch.org/docs/stable/nn.html
        """
        return getattr(nn, layer_type)(*layer_params, **layer_kwargs)
