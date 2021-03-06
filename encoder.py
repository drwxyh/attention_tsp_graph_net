from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import tensorflow as tf
from graph_nets import graphs
from graph_nets import blocks
from graph_nets import modules
import sonnet as snt
import utils


class MultiHeadAttentionResidual(snt.AbstractModule):
    """MultiHeadAttention layer

    This modules computes the values, key and query for a `graphs.GraphsTuples` nodes and then calls
    `modules.selfAttention`
    """

    def __init__(self, conf, name="multi_head_attention"):
        """ Inits the module.

               Args:
                   name: The module name.
               """
        super(MultiHeadAttentionResidual, self).__init__(name=name)
        self.training = True
        self.conf = conf
        self.training = True
        with self._enter_variable_scope():
            self._query_layer = snt.Linear(output_size=self.conf.head_nbr * self.conf.query_dim,
                                           use_bias=False,
                                           initializers={'w': utils.initializer(conf.embedding_dim)},
                                           name="query_computer")
            self._value_layer = snt.Linear(output_size=self.conf.head_nbr * self.conf.value_dim,
                                           use_bias=False,
                                           initializers={'w': utils.initializer(conf.embedding_dim)},
                                           name="value_computer")
            self._key_layer = snt.Linear(output_size=self.conf.head_nbr * self.conf.key_dim,
                                         use_bias=False,
                                         initializers={'w': utils.initializer(conf.embedding_dim)},
                                         name="key_computer")
            self._graph_mha = modules.SelfAttention("graph_self_attention")
            self._glimpse_l = snt.Linear(name='glimpse_linear',
                                         output_size=self.conf.embedding_dim,
                                         use_bias=False,
                                         initializers={'w': utils.initializer(conf.embedding_dim)})

    def modify_state(self, training=True):
        self.training = training

    def load(self, dic, sess):
        with self._enter_variable_scope():
            utils.load_linear(self._query_layer, dic["query_computer"], sess)
            utils.load_linear(self._value_layer, dic["value_layer"], sess)
            utils.load_linear(self._key_layer, dic["key_layer"], sess)
            utils.load_linear(self._glimpse_l, dic["glimpse_linear"], sess)

    def save(self, sess):
        with self._enter_variable_scope():
            dic = {
                "query_computer": utils.save_linear(self._query_layer, sess),
                "value_layer": utils.save_linear(self._value_layer, sess),
                "key_layer": utils.save_linear(self._key_layer, sess),
                "glimpse_linear": utils.save_linear(self._glimpse_l, sess),
            }
        return dic

    def _build(self, graph):
        """Perform a Multi Head Attention over a graph

        Args:
            graph (graphs.GraphsTuple): The graph over which the multi head attention will be performed.

        Returns:
            graphs.GraphsTuple

        """
        # assert tf.reduce_all(tf.equal(graph.n_node[0], graph.n_node)), "Not all the graphs have the same size!"

        nodes = graph.nodes

        query = tf.reshape(self._query_layer(nodes), (-1, self.conf.head_nbr, self.conf.query_dim))
        value = tf.reshape(self._value_layer(nodes), (-1, self.conf.head_nbr, self.conf.value_dim))
        key = tf.reshape(self._key_layer(nodes), (-1, self.conf.head_nbr, self.conf.key_dim))

        attention_result = self._graph_mha(value, key, query, graph)
        nodes_a = attention_result.nodes
        new_nodes = self._glimpse_l(tf.reshape(nodes_a, (self.conf.batch * self.conf.n_node, self.conf.embedding_dim)))
        # new_nodes = tf.einsum('ijk,kl->ijl', nodes_a, self._W)
        # new_nodes = tf.reduce_sum(new_nodes, axis=1)
        return attention_result.replace(nodes=tf.add(new_nodes, nodes))


class EncoderLayer(snt.AbstractModule):
    """Layer for the Encoding module.

    This layer contains:

    - Multi-head attention
    - Fully connected Feed-Forward network
    Each layer adds a skip-connection and a batch normalization
    """

    def __init__(self, conf, name="encoder_layer"):
        """ Inits the module.

        Args:
            name: The module name.
        """
        super(EncoderLayer, self).__init__(name=name)
        self.conf = conf
        self.training = True
        with self._enter_variable_scope():
            batch_initializer = {'gamma': utils.initializer(conf.embedding_dim),
                                 'moving_mean': utils.initializer(conf.embedding_dim),
                                 'moving_variance': utils.initializer(conf.embedding_dim),
                                 'beta': utils.initializer(conf.embedding_dim)}

            self._mha = MultiHeadAttentionResidual(conf)

            self._batch_norm0 = snt.BatchNormV2(scale=True,
                                                initializers=batch_initializer,
                                                name="batch_norm0")

            self._batch_norm1 = snt.BatchNormV2(scale=True,
                                                initializers=batch_initializer,
                                                name="batch_norm1")

            self._lin_to_hidden = snt.Linear(output_size=conf.ff_hidden_size,
                                             initializers={'w': utils.initializer(conf.embedding_dim),
                                                           'b': utils.initializer(conf.embedding_dim)},
                                             name="lin_to_hidden")
            self._hidden_to_ouput = snt.Linear(output_size=conf.embedding_dim,
                                               initializers={'w': utils.initializer(conf.ff_hidden_size),
                                                             'b': utils.initializer(conf.ff_hidden_size)},
                                               name="hidden_to_ouput")
            self._feed_forward = snt.Sequential([self._lin_to_hidden,
                                                 tf.nn.relu,
                                                 self._hidden_to_ouput],
                                                name="feed_forward")
            self._feed_forward_residual = snt.Residual(self._feed_forward, name="feed_forward_residual")

            # self._part_encoder = snt.Sequential([lambda x: self._batch_norm0(x, is_training=self.training),
            #                                      self._feed_forward_residual,
            #                                      lambda x: self._batch_norm1(x, is_training=self.training)],
            #                                     name="full_encoder")
            # self._part_encoder_block = blocks.NodeBlock(lambda: self._part_encoder,
            #                                             use_received_edges=False,
            #                                             use_nodes=True,
            #                                             use_globals=False,
            #                                             name="encoder_block")

    def modify_state(self, training=True):
        self.training = training

    def load(self, dic, sess):
        with self._enter_variable_scope():
            self._mha.load(dic['multi_head_attention'], sess)
            utils.load_batchnorm(self._batch_norm0, dic['batch_norm0'], sess)
            utils.load_batchnorm(self._batch_norm1, dic['batch_norm1'], sess)
            utils.load_linear(self._lin_to_hidden, dic['lin_to_hidden'], sess, bias=True)
            utils.load_linear(self._hidden_to_ouput, dic['hidden_to_output'], sess, bias=True)

    def save(self, sess):
        with self._enter_variable_scope():
            dic = {
                "multi_head_attention": self._mha.save(sess),
                "batch_norm0": utils.save_batchnorm(self._batch_norm0, sess),
                "batch_norm1": utils.save_batchnorm(self._batch_norm1, sess),
                "lin_to_hidden": utils.save_linear(self._lin_to_hidden, sess, bias=True),
                "hidden_to_output": utils.save_linear(self._hidden_to_ouput, sess, bias=True)
            }
        return dic

    def _build(self, graph):
        """

        Args:
            graph (graphs.GraphsTuple):

        Returns:
            graphs.GraphsTuple

        """
        mha_residual_graph = self._mha(graph)
        nodes = tf.reshape(mha_residual_graph.nodes, (self.conf.batch, self.conf.n_node, self.conf.embedding_dim))
        nodes = self._batch_norm0(nodes, is_training=self.training)
        nodes = tf.reshape(nodes, (self.conf.batch * self.conf.n_node, self.conf.embedding_dim))
        nodes = self._feed_forward_residual(nodes)
        nodes = tf.reshape(nodes, (self.conf.batch, self.conf.n_node, self.conf.embedding_dim))
        nodes = self._batch_norm1(nodes, is_training=self.training)
        nodes = tf.reshape(nodes, (self.conf.batch * self.conf.n_node, self.conf.embedding_dim))

        new_graph = graph.replace(nodes=nodes)

        return new_graph


class Encoder(snt.AbstractModule):
    """Encoder for attention-tsp module.

    This modules projects the coordinates into a INIT_self.conf.embedding_dim dimension space
    and then stacks self.conf.encoder_nbr_layers layers of EncoderLayer.
    """

    def __init__(self, conf, name="encoder-attention-tsp"):
        """Inits the module.

        Args:
            name: The module name.
        """

        super(Encoder, self).__init__(name=name)
        self.conf = conf
        self.training = True
        with self._enter_variable_scope():
            self._initial_projection = snt.Linear(output_size=self.conf.embedding_dim,
                                                  initializers={'w': utils.initializer(conf.init_dim),
                                                                'b': utils.initializer(conf.init_dim)},
                                                  name="initial_projection")
            self._initial_projection_block = blocks.NodeBlock(lambda: self._initial_projection,
                                                              use_received_edges=False,
                                                              use_nodes=True,
                                                              use_globals=False,
                                                              name="initial_block_projection")
            self._encoder_layers = [EncoderLayer(conf, "encoder_layer_%i" % i) for i in
                                    range(self.conf.encoder_nbr_layers)]

    def modify_state(self, training=True):
        self.training = training

    def load(self, dic, sess):
        with self._enter_variable_scope():
            utils.load_linear(self._initial_projection, dic['initial_projection'], sess, bias=True)
            for i in range(self.conf.encoder_nbr_layers):
                self._encoder_layers[i].load(dic["encoder_layer_%i" % i], sess)

    def save(self, sess):
        with self._enter_variable_scope():
            dic = {
                'initial_projection': utils.save_linear(self._initial_projection, sess, bias=True),
            }
            for i in range(self.conf.encoder_nbr_layers):
                dic["encoder_layer_%i" % i] = self._encoder_layers[i].save(sess)
        return dic

    def _build(self, graph):
        """Encodes the graph

        Args:
            graph (graphs.GraphsTuple): A `graphs.GraphsTuple` ...

        Returns:
            An ouput `graphs.GraphsTuple` with encoded nodes.
        """

        projected_graph = self._initial_projection_block(graph)
        for encoder in self._encoder_layers:
            projected_graph = encoder(projected_graph)
        # could use block func
        nodes = tf.reshape(projected_graph.nodes, (self.conf.batch, self.conf.n_node, self.conf.embedding_dim))

        return projected_graph.replace(globals=tf.reduce_mean(nodes, axis=1))
