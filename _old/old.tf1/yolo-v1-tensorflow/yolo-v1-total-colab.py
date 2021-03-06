"""
[WARNING]
If you want to execute this file in google colab env,
you should restart runtime session!!!!!!
"""

import tensorflow as tf
import numpy as np
import cv2
import os
import random
import xml.etree.ElementTree as xml
import time
# from matplotlib import pyplot as plt

# weight_file = None
weight_file = "/content/drive/My Drive/Saver/epoch6/4lab_detection1_Epoch_6.ckpt"

image_Width = 448
image_Height = 448
channel = 3
label_size = 20  # pascal VOC 2012 Dataset
grid = 7
batchsize = 8
Learning_Rate = 0.00001

box_per_cell = 2  # one cell have 2 box
boundary1 = grid * grid * label_size
boundary2 = boundary1 + grid * grid * box_per_cell  # 7 * 7 * 20 + 7 * 7 *2

relu_alpha = 0.5

TrainDir = "/content/drive/My Drive/voc2012/VOC2012/JPEGImages/"  # 300,000 images
TrainDir_Annot = "/content/drive/My Drive/voc2012/VOC2012/Annotations/"

# The names of this variables(=ModelDir, ModelName) must come from the script name.
ModelName = "4lab_detection1"
ModelDir = "/content/drive/My Drive/" + ModelName

Filenames_Eval = []
Filenames_Train = []

index_train = 0
index_eval = 0

ForEpoch = 40

label_full = ['person', 'bird', 'cat', 'cow', 'dog',
              'horse', 'sheep', 'aeroplane', 'bicycle', 'boat',
              'bus', 'car', 'motorbike', 'train', 'bottle',
              'chair', 'diningtable', 'pottedplant', 'sofa', 'tvmonitor']

label_size = 20  # => len(label_full)
Total_Train = 17125

relu_alpha = 0.5

object_scale = 1.0
noobject_scale = 1.0
class_scale = 2.0
coord_scale = 5.0

keep_prob = 0.5


def sigmoid(x):
    y = tf.math.sigmoid(x)

    return y


def batch_norm(input, n_out, training, scope='bn'):
    with tf.compat.v1.variable_scope(scope):
        beta = tf.Variable(tf.constant(0.0, shape=[n_out]), name='beta', trainable=True)
        gamma = tf.Variable(tf.constant(1.0, shape=[n_out]), name='gamma', trainable=True)
        batch_mean, batch_var = tf.nn.moments(input, [0, 1, 2], name='moments')
        ema = tf.train.ExponentialMovingAverage(decay=0.5)

        def mean_var_with_update():
            ema_apply_op = ema.apply([batch_mean, batch_var])
            with tf.control_dependencies([ema_apply_op]):
                return tf.identity(batch_mean), tf.identity(batch_var)

        mean, var = tf.cond(training, true_fn=mean_var_with_update,
                            false_fn=lambda: (ema.average(batch_mean), ema.average(batch_var)))
        normed = tf.nn.batch_normalization(input, mean, var, beta, gamma, 1e-3)
    return normed


############


def block_conv(input, ksize, ch_input, output_ch, stride, istraining, name):
    ksize = [ksize, ksize, int(ch_input), output_ch]
    strides = [1, stride, stride, 1]
    n_out = ksize[-1]

    kernel = tf.Variable(tf.random.truncated_normal(ksize, stddev=0.1), name=name + '_weight')
    conv = tf.nn.conv2d(input, kernel, strides, padding='SAME')
    bn = batch_norm(conv, n_out=n_out, training=istraining)
    conv = tf.nn.leaky_relu(bn, alpha=relu_alpha, name=name + '_leaky-RELU')
    b, h, w, c = conv.shape
    print(name + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

    return conv


def block_residual(input, output_ch1, output_ch2, stride, istraining, name):
    ksize1 = [1, 1, int(input.shape[-1]), output_ch1]
    ksize2 = [3, 3, output_ch1, output_ch2]
    strides = [1, stride, stride, 1]

    kernel1 = tf.Variable(tf.random.truncated_normal(ksize1, stddev=0.1), name=name + '_weight_1')
    conv1 = tf.nn.conv2d(input, kernel1, strides, padding='SAME')
    bn1 = batch_norm(conv1, n_out=ksize1[-1], training=istraining)
    af1 = tf.nn.leaky_relu(bn1, alpha=relu_alpha, name=name + '_leaky-RELU')

    b, h, w, c = af1.shape
    print(name + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

    kernel2 = tf.Variable(tf.random.truncated_normal(ksize2, stddev=0.1), name=name + '_weight_2')
    conv2 = tf.nn.conv2d(af1, kernel2, strides, padding='SAME')
    bn2 = batch_norm(conv2, n_out=ksize2[-1], training=istraining)
    af2 = tf.nn.leaky_relu(bn2, alpha=relu_alpha, name=name + '_leaky-RELU')

    b, h, w, c = af2.shape
    print(name + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

    return af2


def block_upsample(input, name, method="deconv"):
    assert method in ["resize", "deconv"]

    with tf.compat.v1.variable_scope(name):
        if method == "resize":  # case in resize
            input_shape = tf.shape(input)
            output = tf.image.resize_nearest_neighbor(input, (input_shape[1] * 2, input_shape[2] * 2))

            return output

        if method == "deconv":  # case in deconvolution(transpose)
            # replace resize_nearest_neighbor with conv2d_transpose To support TensorRT optimization
            numm_filter = input.shape.as_list()[-1]
            output = tf.layers.conv2d_transpose(input, numm_filter, kernel_size=2, padding='same',
                                                strides=(2, 2), kernel_initializer=tf.random_normal_initializer())
            return output


def block_maxpool(input, name):
    ret = tf.nn.max_pool2d(input, ksize=[1, 2, 2, 1], strides=[1, 2, 2, 1], padding='SAME', name=name)
    b, h, w, c = ret.shape
    print(name + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

    return ret


class network:
    """
    Builds Darknet-53 model.
    """

    def __init__(self, imgs, training, weights=None, sess=None):
        self.label_size = label_size
        self.imgs = imgs
        self.training = training
        self.grid = grid
        self.keep_prob = keep_prob
        self.convlayers()
        # self.gap_layers()
        self.fc_layers()
        # self.probs = tf.nn.softmax(self.fc)
        if weights is not None and sess is not None:
            self.load_weights(weights, sess)

    def convlayers(self):
        self.parameters = []
        self.layers = {}

        # initialization input node
        self.imgs = tf.reshape(self.imgs, shape=[-1, image_Height, image_Width, 3], name='input_node')
        ########################################################################################################################
        # conv1
        with tf.name_scope('conv1') as scope:
            self.conv1 = block_conv(self.imgs, ksize=7,
                                    ch_input=self.imgs.shape[-1],
                                    output_ch=64,
                                    stride=2,
                                    istraining=self.training,
                                    name=scope[:-1])

        # maxpool1
        with tf.name_scope('pool1') as scope:
            self.pool1 = block_maxpool(self.conv1, name=scope)

        # conv2
        with tf.name_scope('conv2') as scope:
            self.conv2 = block_conv(self.pool1, ksize=3,
                                    ch_input=self.pool1.shape[-1],
                                    output_ch=192,
                                    stride=1,
                                    istraining=self.training,
                                    name=scope[:-1])

        # maxpool2
        with tf.name_scope('pool2') as scope:
            self.pool2 = block_maxpool(self.conv2, name=scope)

        # conv3
        with tf.name_scope('conv3') as scope:
            self.conv3 = block_residual(self.pool2,
                                        output_ch1=128,
                                        output_ch2=256,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # conv4
        with tf.name_scope('conv4') as scope:
            self.conv4 = block_residual(self.conv3,
                                        output_ch1=256,
                                        output_ch2=512,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # maxpool3
        with tf.name_scope('pool3') as scope:
            self.pool3 = block_maxpool(self.conv4, name=scope)

        # conv5
        with tf.name_scope('conv5') as scope:
            self.conv5 = block_residual(self.pool3,
                                        output_ch1=256,
                                        output_ch2=512,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # conv6
        with tf.name_scope('conv6') as scope:
            self.conv6 = block_residual(self.conv5,
                                        output_ch1=256,
                                        output_ch2=512,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # conv7
        with tf.name_scope('conv7') as scope:
            self.conv7 = block_residual(self.conv6,
                                        output_ch1=256,
                                        output_ch2=512,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # conv8
        with tf.name_scope('conv8') as scope:
            self.conv8 = block_residual(self.conv7,
                                        output_ch1=256,
                                        output_ch2=512,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # conv9
        with tf.name_scope('conv9') as scope:
            self.conv9 = block_residual(self.conv8,
                                        output_ch1=512,
                                        output_ch2=1024,
                                        stride=1,
                                        istraining=self.training,
                                        name=scope)

        # maxpool4
        with tf.name_scope('pool4') as scope:
            self.pool4 = block_maxpool(self.conv9, name=scope)

        # conv10
        with tf.name_scope('conv10') as scope:
            self.conv10 = block_residual(self.pool4,
                                         output_ch1=512,
                                         output_ch2=1024,
                                         stride=1,
                                         istraining=self.training,
                                         name=scope)

        # conv11
        with tf.name_scope('conv11') as scope:
            self.conv11 = block_residual(self.conv10,
                                         output_ch1=512,
                                         output_ch2=1024,
                                         stride=1,
                                         istraining=self.training,
                                         name=scope)

        # conv12
        with tf.name_scope('conv12') as scope:
            self.conv12 = block_conv(self.conv11, ksize=3,
                                     ch_input=self.conv11.shape[-1],
                                     output_ch=1024,
                                     stride=1,
                                     istraining=self.training,
                                     name=scope[:-1])

        # conv13
        with tf.name_scope('conv13') as scope:
            self.conv13 = block_conv(self.conv12, ksize=3,
                                     ch_input=self.conv12.shape[-1],
                                     output_ch=1024,
                                     stride=2,
                                     istraining=self.training,
                                     name=scope[:-1])

        # conv14
        with tf.name_scope('conv14') as scope:
            self.conv14 = block_conv(self.conv13, ksize=3,
                                     ch_input=self.conv13.shape[-1],
                                     output_ch=1024,
                                     stride=1,
                                     istraining=self.training,
                                     name=scope[:-1])

        # conv15
        with tf.name_scope('conv15') as scope:
            self.conv15 = block_conv(self.conv14, ksize=3,
                                     ch_input=self.conv14.shape[-1],
                                     output_ch=1024,
                                     stride=1,
                                     istraining=self.training,
                                     name=scope[:-1])

    def fc_layers(self):
        with tf.name_scope('fc1') as scope:
            # ksize = [1, 1, 512, 512]
            # strides = [1, 1, 1, 1]
            ksize = [int(self.conv15.shape[1] * self.conv15.shape[2] * self.conv15.shape[3]), 4096]

            kernel = tf.Variable(tf.random.truncated_normal(ksize, stddev=0.1), name='weights_fc1')
            # conv = tf.nn.conv2d(self.gap, kernel, strides, padding='SAME')
            fc1 = tf.reshape(self.conv15,
                             shape=[-1, self.conv15.shape[1] * self.conv15.shape[2] * self.conv15.shape[3]])
            fc2 = tf.matmul(fc1, kernel)
            self.fc1 = fc2
            b, c = self.fc1.shape
            # b, h, w, c = self.fc1.shape
            print(scope[:-1] + " output ->", "[" + str(b) + ", " + str(c) + "]")
            # print(scope[:-1] + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

        with tf.name_scope('dropout') as scope:
            self.dropout = tf.nn.dropout(self.fc1, rate=self.keep_prob)
            b, c = self.dropout.shape
            print(scope[:-1] + " output ->", "[" + str(b) + ", " + str(c) + "]")

        with tf.name_scope('fc2') as scope:
            # ksize = [1, 1, 512, 512]
            # strides = [1, 1, 1, 1]
            ksize = [int(self.dropout.shape[-1]), grid * grid * (box_per_cell * 5 + self.label_size)]

            kernel = tf.Variable(tf.random.truncated_normal(ksize, stddev=0.1), name='weights_fc2')
            # conv = tf.nn.conv2d(self.gap, kernel, strides, padding='SAME')
            # fc1 = tf.reshape(self.fc1, shape=[-1, self.conv15.shape[-1]])
            fc2 = tf.matmul(self.dropout, kernel)
            # fc3 = tf.reshape(fc2, shape=[-1, grid, grid, (2 * 5 + self.label_size)])
            self.fc2 = fc2
            b, c = self.fc2.shape
            # b, h, w, c = self.fc2.shape
            print(scope[:-1] + " output ->", "[" + str(b) + ", " + str(c) + "]")
            # print(scope[:-1] + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

        with tf.name_scope('output') as scope:
            self.output = tf.reshape(self.fc2, shape=[-1, grid, grid, 5 * box_per_cell + self.label_size],
                                     name='output')
            b, h, w, c = self.output.shape
            print(scope[:-1] + " output ->", "[" + str(h) + ", " + str(w) + ", " + str(c) + "]")

    def load_weights(self, weight_file, sess):
        print(f"Weight Loading Start! -> {weight_file}")
        saver = tf.compat.v1.train.Saver()  # Network model Save
        meta_saver = tf.train.import_meta_graph(weight_file + ".meta")
        save_path = saver.restore(sess, weight_file)
        print(f"Weight Loading is successful")


def calc_iou(boxes1, boxes2, scope='iou'):
    """calculate ious
    Args:
      boxes1: 5-D tensor [BATCH_SIZE, CELL_SIZE, CELL_SIZE, BOXES_PER_CELL, 4]  ====> (x_center, y_center, w, h)
      boxes2: 5-D tensor [BATCH_SIZE, CELL_SIZE, CELL_SIZE, BOXES_PER_CELL, 4] ===> (x_center, y_center, w, h)
    Return:
      iou: 4-D tensor [BATCH_SIZE, CELL_SIZE, CELL_SIZE, BOXES_PER_CELL]
    """
    with tf.compat.v1.variable_scope(scope):
        # transform (x_center, y_center, w, h) to (x1, y1, x2, y2)
        boxes1_t = tf.stack([boxes1[..., 0] - boxes1[..., 2] / 2.0,
                             boxes1[..., 1] - boxes1[..., 3] / 2.0,
                             boxes1[..., 0] + boxes1[..., 2] / 2.0,
                             boxes1[..., 1] + boxes1[..., 3] / 2.0],
                            axis=-1)

        boxes2_t = tf.stack([boxes2[..., 0] - boxes2[..., 2] / 2.0,
                             boxes2[..., 1] - boxes2[..., 3] / 2.0,
                             boxes2[..., 0] + boxes2[..., 2] / 2.0,
                             boxes2[..., 1] + boxes2[..., 3] / 2.0],
                            axis=-1)

        # calculate the left up point & right down point
        lu = tf.maximum(boxes1_t[..., :2], boxes2_t[..., :2])
        rd = tf.minimum(boxes1_t[..., 2:], boxes2_t[..., 2:])

        # intersection
        intersection = tf.maximum(0.0, rd - lu)
        inter_square = intersection[..., 0] * intersection[..., 1]

        # calculate the boxs1 square and boxs2 square
        square1 = boxes1[..., 2] * boxes1[..., 3]
        square2 = boxes2[..., 2] * boxes2[..., 3]

        union_square = tf.maximum(square1 + square2 - inter_square, 1e-10)

    return tf.clip_by_value(inter_square / union_square, 0.0, 1.0)


def loss_layer(predicts, labels, scope='loss_layer'):
    global object_scale
    global noobject_scale
    global class_scale
    global coord_scale

    with tf.compat.v1.variable_scope(scope):
        print(boundary1)
        print(predicts[:, :boundary1])
        predict_classes = tf.reshape(predicts[:, :boundary1], [batchsize, grid, grid, label_size])
        predict_scales = tf.reshape(predicts[:, boundary1:boundary2], [batchsize, grid, grid, box_per_cell])
        predict_boxes = tf.reshape(predicts[:, boundary2:], [batchsize, grid, grid, box_per_cell, 4])

        response = tf.reshape(labels[..., 0], [batchsize, grid, grid, 1])  # response = confidence score
        boxes = tf.reshape(labels[..., 1:5], [batchsize, grid, grid, 1, 4])
        boxes = tf.tile(boxes, [1, 1, 1, box_per_cell, 1]) / image_Width
        classes = labels[..., 5:]
        offset = np.transpose(np.reshape(np.array([np.arange(grid)] * grid * box_per_cell), (box_per_cell, grid, grid)),
                              (1, 2, 0))
        offset = tf.reshape(tf.constant(offset, dtype=tf.float32),
                            [1, grid, grid, box_per_cell])  # Reshape from 7*7*2 to 1*7*7*2
        offset = tf.tile(offset, [batchsize, 1, 1, 1])  # Copy on the first dimension and become [batchsize, 7, 7, 2]
        offset_tran = tf.transpose(offset, (0, 2, 1, 3))  # The dimension is [batchsize, 7, 7, 2]

        predict_boxes_tran = tf.stack(
            [(predict_boxes[..., 0] + offset) / grid,
             (predict_boxes[..., 1] + offset_tran) / grid,
             tf.square(predict_boxes[..., 2]),
             tf.square(predict_boxes[..., 3])], axis=-1)

        iou_predict_truth = calc_iou(predict_boxes_tran, boxes)

        # calculate I tensor [batchsize, CELL_SIZE, CELL_SIZE, BOXES_PER_CELL]
        object_mask = tf.reduce_max(iou_predict_truth, 3, keep_dims=True)
        object_mask = tf.cast((iou_predict_truth >= object_mask), tf.float32) * response

        # calculate no_I tensor [CELL_SIZE, CELL_SIZE, BOXES_PER_CELL]
        noobject_mask = tf.ones_like(object_mask, dtype=tf.float32) - object_mask

        boxes_tran = tf.stack(
            [boxes[..., 0] * grid - offset,
             boxes[..., 1] * grid - offset_tran,
             tf.sqrt(boxes[..., 2]),
             tf.sqrt(boxes[..., 3])], axis=-1)

        # class_loss, calculate the loss of the category
        class_delta = response * (predict_classes - classes)
        class_loss = tf.reduce_mean(tf.reduce_sum(tf.square(class_delta), axis=[1, 2, 3]),
                                    name='class_loss') * class_scale

        object_delta = object_mask * (predict_scales - iou_predict_truth)
        object_loss = tf.reduce_mean(tf.reduce_sum(tf.square(object_delta), axis=[1, 2, 3]),
                                     name='object_loss') * object_scale

        noobject_delta = noobject_mask * predict_scales
        noobject_loss = tf.reduce_mean(tf.reduce_sum(tf.square(noobject_delta), axis=[1, 2, 3]),
                                       name='noobject_loss') * noobject_scale

        coord_mask = tf.expand_dims(object_mask, 4)
        boxes_delta = coord_mask * (predict_boxes - boxes_tran)
        coord_loss = tf.reduce_mean(tf.reduce_sum(tf.square(boxes_delta), axis=[1, 2, 3, 4]),
                                    name='coord_loss') * coord_scale

        tf.compat.v1.losses.add_loss(class_loss)
        tf.compat.v1.losses.add_loss(object_loss)
        tf.compat.v1.losses.add_loss(noobject_loss)
        tf.compat.v1.losses.add_loss(coord_loss)

        tf.compat.v1.summary.scalar('class_loss', class_loss)
        tf.compat.v1.summary.scalar('object_loss', object_loss)
        tf.compat.v1.summary.scalar('noobject_loss', noobject_loss)
        tf.compat.v1.summary.scalar('coord_loss', coord_loss)

        tf.compat.v1.summary.histogram('boxes_delta_x', boxes_delta[..., 0])
        tf.compat.v1.summary.histogram('boxes_delta_y', boxes_delta[..., 1])
        tf.compat.v1.summary.histogram('boxes_delta_w', boxes_delta[..., 2])
        tf.compat.v1.summary.histogram('boxes_delta_h', boxes_delta[..., 3])
        tf.compat.v1.summary.histogram('iou', iou_predict_truth)

    return class_loss + object_loss + noobject_loss + coord_loss


def load_image():
    global Filenames_Train
    print("###############################################")
    print("Start Image Loading ...")
    print("###############################################")
    filelist = os.listdir(TrainDir)
    print(f'Total number of Images : {len(filelist)}')

    for f in range(0, len(filelist)):
        Filenames_Train.append(filelist[f])
    random.shuffle(Filenames_Train)
    print(Filenames_Train)
    print("Finish Image Loading !")
    print("###############################################")


def batch_train(batchsize):
    global index_train
    x_data = np.zeros([batchsize, image_Width, image_Height, channel], dtype=np.float32)
    y_data = np.zeros((batchsize, grid, grid, 5 + label_size), dtype=np.float32)  # -> one hot encoding
    for i in range(0, batchsize):
        image_real_path = TrainDir + Filenames_Train[index_train + i]
        annot_real_path = TrainDir_Annot + Filenames_Train[index_train + i][:-4] + '.xml'

        xmin = 0
        ymin = 0
        xmax = 0
        ymax = 0
        templist = list()
        tree = xml.ElementTree(file=annot_real_path)
        for elem in tree.iter(tag='object'):
            label = list(elem)[0].text
            for tag in range(0, len(list(elem))):
                if list(elem)[tag].tag == 'bndbox':
                    for count in range(0, len(list(list(elem)[tag]))):
                        if list(list(elem)[tag])[count].tag == 'xmin':
                            xmin = int(float(list(list(elem)[tag])[count].text))
                        elif list(list(elem)[tag])[count].tag == 'ymin':
                            ymin = int(float(list(list(elem)[tag])[count].text))
                        elif list(list(elem)[tag])[count].tag == 'xmax':
                            xmax = int(float(list(list(elem)[tag])[count].text))
                        elif list(list(elem)[tag])[count].tag == 'ymax':
                            ymax = int(float(list(list(elem)[tag])[count].text))
                    templist.append([xmin, ymin, xmax, ymax, label_full.index(label)])
                else:
                    continue
            else:
                continue
        value = cv2.imread(image_real_path)

        original_h = value.shape[0]  # height = row
        original_w = value.shape[1]  # width = column(=col)

        value = cv2.resize(value, (image_Height, image_Width))
        value = cv2.cvtColor(value, cv2.COLOR_BGR2RGB).astype(np.float32)
        value = (value / 255.0) * 2.0 - 1.0

        size_x = float(image_Width / grid)
        size_y = float(image_Height / grid)

        """
        You should put label value like this form : [1, x_center, y_center, width, height] <- object center grid
        """
        for count in templist:
            xmin = count[0]
            ymin = count[1]
            xmax = count[2]
            ymax = count[3]
            label = count[4]

            # Update coordinate (original -> [448, 448])

            center_x = (xmin + xmax) / 2.0
            center_y = (ymin + ymax) / 2.0
            box_width = max(xmin, xmax) - min(xmin, xmax)
            box_height = max(ymin, ymax) - min(ymin, ymax)

            new_center_x = center_x * image_Width / original_w
            new_center_y = center_y * image_Height / original_h
            new_box_width = box_width * image_Width / original_w
            new_box_height = box_height * image_Height / original_h

            offset_x = new_center_x / size_x
            offset_y = new_center_y / size_y
            label_value = np.zeros(shape=(5 + label_size))
            label_value[0] = 1
            label_value[1] = new_center_x
            label_value[2] = new_center_y
            label_value[3] = new_box_width
            label_value[4] = new_box_height
            label_value[5 + label] = 1
            y_data[i, int(offset_x), int(offset_y), :] = label_value
        x_data[i, :, :, :] = value
    index_train = index_train + batchsize
    if index_train + batchsize >= Total_Train:
        index_train = 0

    return x_data, y_data


############

load_image()

with tf.compat.v1.Session() as sess:
    X = tf.compat.v1.placeholder(tf.float32, [batchsize, image_Width, image_Height, channel], name='input')
    Y = tf.compat.v1.placeholder(tf.float32, [batchsize, grid, grid, 5 + label_size], name='label')
    istraining = tf.compat.v1.placeholder(tf.bool, name='istraining')

    result = network(X, istraining)
    print(f" *** result={result.fc2}")

    loss_layer(predicts=result.fc2, labels=Y)
    loss = tf.compat.v1.losses.get_total_loss()
    print(f" *** loss = {loss}")
    optimizer = tf.train.AdamOptimizer(learning_rate=Learning_Rate)
    global_step = tf.train.create_global_step()

    train_step = tf.contrib.training.create_train_op(loss, optimizer, global_step=global_step)

    tf.compat.v1.summary.scalar("loss", loss)

    merge_summary_op = tf.compat.v1.summary.merge_all()
    merged = tf.compat.v1.summary.merge_all()

    sess.run(tf.compat.v1.global_variables_initializer())
    saver = tf.compat.v1.train.Saver()  # Network model Save
    writer = tf.compat.v1.summary.FileWriter(ModelDir + "logs", sess.graph)

    if weight_file is not None:
        print(f"Weight file Loading Start! -> {weight_file}")
        meta_saver = tf.train.import_meta_graph(weight_file + ".meta")
        save_path = saver.restore(sess, weight_file)
        print(f"Weight file Loading is successful")

    # ==========================================================================================================
    # Training!
    # ==========================================================================================================
    for epoch in range(0, ForEpoch):  # epoch 1 ~ epoch 20
        count = 0
        for i in range(int(Total_Train / batchsize)):
            # for i in range(1):
            count += 1
            bx, by = batch_train(batchsize)
            bx = np.reshape(bx, [batchsize, image_Width, image_Height, channel])
            cost, _ = sess.run([loss, train_step], feed_dict={X: bx, Y: by, istraining.name: True})

            now = time.localtime()
            print("%04d/%02d/%02d %02d:%02d:%02d" % (
                now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec))

            print('[' + str(count) + '] ',
                  'Epoch %d    ' % (epoch + 1),
                  # 'Training accuracy %g     ' % train_accuracy,
                  'loss %g        ' % cost)

        save_path2 = saver.save(sess, ModelDir + "\\" + ModelName + "_Epoch_" + str(epoch + 1) + ".ckpt")
        tf.io.write_graph(sess.graph_def, ModelDir, "trained_" + ModelName + ".pb", as_text=False)

        # count = 0
        #
        # for j in range(int(Total_Eval / batchsize)):
        #     count += 1
        #     Start = time.time()  # For Time Checking!
        #     ex, ey = batch_eval(batchsize)
        #     ex = np.reshape(ex, [batchsize, image_Width, image_Height, channel])
        #     l, y, acc, summary = sess.run([ly, ay, accuracy, merged], feed_dict={X: ex, Y: ey, istraining.name: False})
        #     writer.add_summary(summary)
        #
        #     End = time.time() - Start
        #     print('[' + str(count) + '] ', "epoch ", (epoch + 1), "     mini accuracy : ", acc, "     mini time(sec) : ", End)
        #     total_time = total_time + End
        #     accuracy_sum = accuracy_sum + acc
        #
        # print('Epoch %d' % (epoch + 1), 'test : %f' % (accuracy_sum / (Total_Eval / batchsize) * 100))
        # accuracy_list.append(accuracy_sum / (Total_Eval / batchsize) * 100)
        # accuracy_sum = 0
        # for i in range(0, epoch):
        #     print('Epoch %d' % (i + 1), 'test : %f' % accuracy_list[i])

    print("### Finish Training! ###")
