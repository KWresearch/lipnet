from __future__ import division
from abc import ABCMeta, abstractmethod
import pandas as pd
from random import shuffle
import os
from skimage import io, img_as_float
from skimage.transform import resize
import numpy as np
import sys
import math
import confusion_matrix as cf
from smote import smote
import math
import helpers

class Batch:
    def __init__(self, data, labels, ids):
        """

        :param data:
        :param labels:
        :param ids:
        """
        assert data.shape[0] == labels.shape[0], "Number of data and corresponding labels must be the same"
        assert data.shape[0] == ids.shape[0], "Number of data and corresponding ids must be the same"
        self.images = data
        self.labels = labels
        self.ids = ids

    @property
    def size(self):
        return self.ids.shape[0]


class DatasetAbstract(object):
    """
    This is an abstract class that describes a dataset object.
    Any implementation of lipnet dataset must follow contract described here
    and implement all stated below methods.
    """
    __metaclass__ = ABCMeta

    @abstractmethod
    def get_num_classes(self):
        """

        :return: integer, number of classes
        """
        pass

    @abstractmethod
    def get_count(self):
        """

        :return: integer, total number of examples in the dataset
        """
        pass

    @abstractmethod
    def iterate_minibatches(self, shuffle_=False):
        pass


class DatasetPD(DatasetAbstract):
    """
    This is an implementation of lipnet dataset based on pandas dataframe and JSON
    """

    @classmethod
    def from_json(cls, path_to_json, path_to_img, batch_size=100, num_epochs=None, image_width=28, image_height=28,
                 path_to_output='./output/', verbose=False):
        """

        :param path_to_json: full path to JSON file to be read into pandas dataframe
        :param path_to_img: full path to folder with images
        :param batch_size: integer, size of batch
        :param num_epochs: integer, number of times whole dataset can be processed, leave NOne for unlimited
        :param image_width:
        :param image_height:
        :param path_to_output:
        :param verbose:
        """
        df = cls.dataframe_from_json(path_to_json, path_to_img)
        return cls(df, batch_size=batch_size, num_epochs=num_epochs,
                      image_width=image_width, image_height=image_height,
                      path_to_output=path_to_output, verbose=verbose)

    def __init__(self, df, batch_size=100, num_epochs=None, image_width=28, image_height=28,
                 path_to_output='./output/', verbose=False):
        """

        :param path_to_json: full path to JSON file to be read into pandas dataframe
        :param path_to_img: full path to folder with images
        :param batch_size: integer, size of batch
        :param num_epochs: integer, number of times whole dataset can be processed, leave NOne for unlimited
        """
        super(DatasetPD, self).__init__()
        self.verbose = verbose
        self.path_to_output = path_to_output

        self._df = df.copy()
        self._shape = self._df.shape

        self._class_columns = [col for col in list(self._df) if col.startswith('Label')]
        if len(self._class_columns) == 0:
            self._df = pd.concat([self._df, pd.get_dummies(self._df['Class'], prefix='Label')], axis=1)
            self._class_columns = [col for col in list(self._df) if col.startswith('Label')]

        # do label smoothing
        # as described in Deep Learning book section 7.5.1
        eps = 0.1
        ix = self._class_columns
        self._df[ix] = self._df[ix] * (1 - eps) + (1 - self._df[ix]) * eps / (len(ix) - 1)

        self.num_epochs = num_epochs
        self._epoch_count = 0
        self._image_height = image_height
        self._image_width = image_width

        if batch_size is not None:
            self._batch_size = batch_size
        else:
            self._batch_size = self.get_count()
        self._chunks = None

        self._prediction_columns = [c + '_prediction' for c in self._class_columns]
        for col in self._prediction_columns:
            self._df[col] = 0

    @property
    def num_steps(self):
        if self.num_epochs is None:
            return sys.maxint
        return int(self.num_epochs * math.ceil(self.get_count() / self._batch_size))

    @property
    def batches_count(self):
        return int(math.ceil(self.get_count() / self._batch_size))

    @property
    def num_batches(self):
        """
        Returns number of batches per epoch
        :return: int
        """
        return math.ceil(self.get_count() / self._batch_size)

    @property
    def confusion_matrix(self):
        return cf.ConfusionMatrix(self._df[self._prediction_columns].values,
                                  self._df[self._class_columns].values)

    def add_dataframe(self, df):
        """
        Add a pandas dataframe to dataset
        :param df: pandas dataframe
        :return: nothing
        """
        class_columns = [col for col in list(df) if col.startswith('Label')]
        if len(class_columns) == 0:
            df = pd.concat([df, pd.get_dummies(df['Class'], prefix='Label')], axis=1)
        self._df = self._df.append(df, ignore_index=True)
        self._df.fillna(value=0, inplace=True)
        self._shape = self._df.shape

    @staticmethod
    def dataframe_from_json(path_to_json, path_to_img):
        df = pd.read_json(path_to_json)
        df['Image'] = path_to_img + df['Image'].astype(str)
        return df

    def iterate_minibatches(self, shuffle_=False):
        ids = self._df.Id.values
        if shuffle_:
            np.random.shuffle(ids)
        start_idx = 0
        while start_idx < ids.shape[0]:
            excerpt = ids[start_idx:start_idx + self._batch_size]
            minibatch = self._get_batch(excerpt)
            start_idx += self._batch_size
            yield minibatch

    def _read_image(self, image_name):
        """
        Read image from self._path_to_img and perform any necessary preparation
        :param image_name: string, image name, which is added to self._path_to_img
        :return: numpy 2d array
        """
        filename = image_name
        try:
            img = io.imread(filename)
        except IOError:
            return None
        img = img_as_float(img)
        if len(img.shape) > 2:
            img = img[:, :, 0]
        img = resize(img, (self._image_width, self._image_height))
        img = img.reshape((self._image_width, self._image_height, 1))
        return img

    def _get_batch(self, ids):
        """
        Creates a batch from example ids
        :param ids: list of int, ids of examples
        :return: an instance of Batch class
        """
        img_names = self._df['Image'][self._df['Id'].isin(ids)]
        images = np.empty([len(img_names), self._image_width, self._image_height, 1], dtype=float)
        i = 0
        for f in img_names:
            img = self._read_image(f)
            if img is not None:
                images[i] = img
                i += 1
            else:
                images = images[:-1, :, :, :]
        labels = self._df[self._class_columns][self._df['Id'].isin(ids)].values
        return Batch(images, labels, np.array(ids))

    def print_stats(self):
        """
        Prints som dataframe statistics to console
        :return: nothing
        """
        print '{} columns and {} rows'.format(self._shape[1], self._shape[0])
        print self._df['Class'].value_counts()

    def get_count(self):
        """
        See description in DatasetAbstract
        :return:
        """
        return self._shape[0]

    def get_num_classes(self):
        """
        See description in DatasetAbstract
        :return:
        """
        return len(self._class_columns)

    def set_predictions(self, ids, predictions):
        """
        Stores predictions in datatframe
        :param ids: list of ints representing ids
        :param predictions: 2d numpy array, number of columns must be equal to number of classes,
                            number of rows must be equal to length of ids
        :return: nothing
        """
        shape = predictions.shape
        assert len(shape) == 2, "Predictions must be a 2d array"
        assert shape[1] == self.get_num_classes(), "Number of classes in dataset and in predictions must be the same"
        assert ids.ndim == 1, "ids must be a vector"
        assert shape[0] == len(ids), "Number of ids and predictions must be the same"
        for i, _id in enumerate(ids):
            try:
                ix = self._df.loc[self._df.Id == _id].index
                self._df.loc[ix, self._prediction_columns] = predictions[i]
            except TypeError:
                pass

    def evaluate(self):
        confusion_matrix = cf.ConfusionMatrix(self._df[self._prediction_columns].values,
                                              self._df[self._class_columns].values)
        confusion_matrix.print_to_console()

    def roc(self):
        """
        Create ROC curve
        :return:
        """
        from sklearn.metrics import roc_curve, auc
        import matplotlib.pyplot as plt
        fpr = dict()
        tpr = dict()
        thresholds = dict()
        roc_auc = dict()
        true_labels = self._df[self._class_columns].values
        predictions = self._df[self._prediction_columns].values
        for i in xrange(self.get_num_classes()):
            fpr[i], tpr[i], thresholds[i] = roc_curve(true_labels[:, i], predictions[:, i])
            roc_auc[i] = auc(fpr[i], tpr[i])

        # Plot of a ROC curve for a specific class
        class_number = 2
        plt.figure()

        for i in xrange(3):
            plt.plot(fpr[i], tpr[i], label='ROC curve of class {0} (area = {1:0.2f})'
                                           ''.format(i, roc_auc[i]))

        plt.plot([0, 1], [0, 1], 'k--')
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate')
        plt.ylabel('True Positive Rate')
        plt.title('Receiver operating characteristic example')
        plt.legend(loc="lower right")
        plt.show()

    def get_image_by_id(self, id):
        """
        Returns real (not synthetic) image matching provided id
        :param name: int
        :return: numpy array
        """
        img_name = self._df[self._df.Id == id].Image.values[0]
        img = self._read_image(img_name)
        img = np.reshape(img, (1, self._image_width, self._image_height, 1))
        return img


class DatasetPDFeatures(DatasetPD):

    def _get_batch(self, ids):
        """
        Creates a batch from example ids
        :param ids: list of int, ids of examples
        :return: an instance of Batch class
        """
        feature_names = ['Area',
                         'Circularity',
                         'DiametersInPixels',
                         'Scale',
                         'Perimeter',
                         'Length',
                         'MembraneThickness'
                         ]
        data = self._df[feature_names][self._df['Id'].isin(ids)].values
        moments = np.array([np.array(xi) for xi in self._df['Moments'][self._df['Id'].isin(ids)].values])
        data = np.concatenate((data, moments), axis=1)
        labels = self._df[self._class_columns][self._df['Id'].isin(ids)].values
        return Batch(data, labels, np.array(ids))

"""

Data augmentation, oversampling and undersampling is implemented in DatasetPDAugmented class
All minority classes are augmented in the following way:
- Examples are rotated 90, 180 and 270 degrees
- Synthetic examples are generated using SMOTE technique
- Random uniform noise is added to minority class examples

Majority class is undersampled by excluding randomly selected examples on each training epoch.

"""


class DatasetPDAugmented(DatasetPD):

    def __init__(self, df, batch_size=100, num_epochs=None, image_width=28, image_height=28,
                 undersampling_rate=0.2, smote_rates=[2], verbose=False, path_to_output='./output/'):
        """

        :param batch_size:
        :param num_epochs:
        :param image_width:
        :param image_height:
        :param undersampling_rate:
        :param smote_rates: list of floats of length >=1, SMOTE rate for all classes except majority class.
        If number of classes is greater then number of rates provided, last rate is used
        :param verbose: boolean, if enabled messages are print and intermediate results are saved for reporting purposes
        """

        super(DatasetPDAugmented, self).__init__(df, batch_size=batch_size, num_epochs=num_epochs,
                                                 image_width=image_width, image_height=image_height, verbose=verbose,
                                                 path_to_output=path_to_output)
        self._undersampling_rate = undersampling_rate
        self._smote_rates = smote_rates
        self._do_augmentation()

    @classmethod
    def from_dataframe(cls, df, batch_size=100, num_epochs=None, image_width=28, image_height=28,
                 undersampling_rate=0.2, smote_rates=[2], verbose=False, path_to_output='./output/'):
        return cls(df, batch_size=batch_size, num_epochs=num_epochs,
                   image_width=image_width, image_height=image_height,
                   verbose=verbose, path_to_output=path_to_output,
                   undersampling_rate=undersampling_rate, smote_rates=smote_rates)

    @classmethod
    def from_json(cls, path_to_json, path_to_img, batch_size=100, num_epochs=None, image_width=28, image_height=28,
                 undersampling_rate=0.2, smote_rates=[2], verbose=False, path_to_output='./output/'):
        df = cls.dataframe_from_json(path_to_json, path_to_img)
        return cls(df, batch_size=batch_size, num_epochs=num_epochs,
                   image_width=image_width, image_height=image_height,
                   verbose=verbose, path_to_output=path_to_output)

    def _do_augmentation(self):
        """
        Do data augmentation
        :return: nothing, new synthetic examples are stored in self._df_synthetic dataframe
        """
        # dataframe to hold synthetic examples
        self._df_synthetic = pd.DataFrame(columns=['Id', 'Image'] + self._class_columns + self._prediction_columns)

        # determine majority and minority classes
        class_counts = self._df['Class'].value_counts()
        self._majority_class = class_counts.index[0]
        self._minority_classes = class_counts.index[1:]

        # undersampling rate tells how many randomly selected example of majority class are ignored on each epoch
        self.undersampling_amount = int(math.ceil(self._undersampling_rate * class_counts[self._majority_class]))

        # augment data
        self._augment(self._minority_classes)

        # generate synthetic examples
        for i, c in enumerate(self._minority_classes):
            try:
                smote_rate = self._smote_rates[i]
            except IndexError:
                smote_rate = self._smote_rates[-1]
            self._oversample(c, smote_rate)
        pass

    def iterate_minibatches(self, shuffle_=False):
        undersampled_ids = self._df.Id.values.copy()
        n = int(math.ceil(undersampled_ids.shape[0] * (1 - self._undersampling_rate)))
        mask = np.random.choice(undersampled_ids.shape[0], n)
        undersampled_ids = undersampled_ids[mask]
        ids = np.concatenate((undersampled_ids, self._df_synthetic.Id.values))
        if shuffle_:
            np.random.shuffle(ids)
        start_idx = 0
        while start_idx < ids.shape[0]:
            excerpt = ids[start_idx:start_idx + self._batch_size]
            minibatch = self._get_batch(excerpt)
            start_idx += self._batch_size
            yield minibatch

    @property
    def num_steps(self):
        if self.num_epochs is None:
            return sys.maxint
        return int(self.num_epochs * math.ceil((self.get_count() - self.undersampling_amount) / self._batch_size))

    def get_count(self):
        real_count = super(DatasetPDAugmented, self).get_count()
        synthetic_count = self._df_synthetic.shape[0]
        return real_count + synthetic_count

    def _augment(self, class_names):
        """
        Perform data augmentation
        :param class_names: string or list of strings, name(s) of class to be augmented
        :return: nothing, new examples as a result of augmentation are stored in self._df_synthetic
        """
        n = self._df['Id'][self._df['Class'].isin(class_names)].count() * 7
        df = pd.DataFrame(index=np.arange(0, n), columns=self._df_synthetic.columns.values)
        i = -1
        for _, example in self._df[self._df['Class'].isin(class_names)].iterrows():
            # read original image
            image = self._read_image(example.Image)
            # flip image
            image_flipped = np.fliplr(image)
            i += 1
            df.loc[i].Id = '{}_{}'.format(example.Id, 0)
            df.loc[i].Image = image_flipped
            df.loc[i][self._class_columns] = example[self._class_columns]

            # rotate image and flipped image 90, 180, 270 degrees
            for j in xrange(1, 4):
                image = np.rot90(image)
                image_flipped = np.rot90(image_flipped)
                # save rotated image to df
                i += 1
                df.loc[i].Id = '{}_{}'.format(example.Id, j)
                df.loc[i].Image = image
                df.loc[i][self._class_columns] = example[self._class_columns]
                # save rotated flipped image to df
                i += 1
                df.loc[i].Id = '{}_{}'.format(example.Id, j + 3)
                df.loc[i].Image = image_flipped
                df.loc[i][self._class_columns] = example[self._class_columns]

        self._df_synthetic = self._df_synthetic.append(df, ignore_index=True)

    def _save_synthetic_examples(self, examples, parents, parent_ids, class_name):
        """
        Saves synthetic images for reporting purposes
        :param examples: array of size (num_examples, image_width*image_height)
        :param parents: array of size (num_examples, image_width*image_height)
        :param parent_ids: array of size (num_examples, 2)
        :param class_name: string
        :return: nothing
        """
        save_dir = os.path.join(self.path_to_output, 'figures/synthetic_examples/{}'.format(class_name))
        helpers.prepare_dir(save_dir, empty=True)
        parents = np.reshape(parents, (-1, self._image_width, self._image_height))
        #parents_resized = parents
        parents_resized = np.zeros((len(parents), 200, 200))
        for i in xrange(len(parents)):
            parents_resized[i] = resize(parents[i], (200, 200))

        i = 0
        for _, img in enumerate(examples):
            i = int(i)
            sys.stdout.write('\rSaving synthetic example {} of {}'.format(i+1, len(examples)))
            sys.stdout.flush()
            img = img.reshape((self._image_height, self._image_width))
            img = resize(img, (200, 200))
            io.imsave(os.path.join(save_dir, '{}_{}_synthetic.png'.format(class_name, i)), img)
            io.imsave(os.path.join(save_dir, '{}_{}_parent_1.png'.format(class_name, i)),
                      parents_resized[parent_ids[i, 0]])
            io.imsave(os.path.join(save_dir, '{}_{}_parent_2.png'.format(class_name, i)),
                      parents_resized[parent_ids[i, 1]])
            i += 1
        sys.stdout.write('\n')

    def _oversample(self, class_name, rate):
        """
        Oversample examples of a class
        :param class_name: string, class name
        :param rate: float, rate of oversampling, 1 corresponds to 100%
        :return: nothing, generated examples are added to self._df_synthetic
        """
        n_examples = self._df['Id'][self._df['Class'].isin([class_name])].count()
        labels = self._df[self._class_columns][self._df['Class'].isin([class_name])].values[0]
        images = np.zeros((n_examples, self._image_height * self._image_width))
        i = 0
        for _, f in self._df.Image[self._df['Class'].isin([class_name])].iteritems():
            img = self._read_image(f)
            images[i] = img.flatten()
            i += 1

        n = math.ceil(n_examples * rate)
        n = int(n)

        #if self.verbose:
        #    synthetic_examples, parent_ids = smote(images, n, n_neighbours=5, return_parent_ids=True)
        #    self._save_synthetic_examples(synthetic_examples, images, parent_ids, class_name)
        #else:
        synthetic_examples = smote(images, n, n_neighbours=5)

        df = pd.DataFrame(index=np.arange(0, n), columns=self._df_synthetic.columns.values)

        for i, img in enumerate(synthetic_examples):
            df.loc[i].Id = 's_{}_{}'.format(class_name, i)
            img = img.reshape((self._image_height, self._image_width))
            df.loc[i].Image = img
            df.loc[i][self._class_columns] = labels

        self._df_synthetic = self._df_synthetic.append(df, ignore_index=True)

    def _get_batch(self, ids):
        """
        Creates a batch from example ids
        :param ids: list of int, ids of examples
        :return: an instance of Batch class
        """

        examples = self._df[['Image', 'Id']][self._df['Id'].isin(ids)]
        images_real = np.empty([examples.shape[0], self._image_width, self._image_height, 1], dtype=float)
        ids_real = np.empty(examples.shape[0])
        i = 0
        for _, example in examples.iterrows():
            images_real[i] = self._read_image(example.Image)
            ids_real[i] = example.Id
            i += 1

        examples = self._df_synthetic[['Image', 'Id']][self._df_synthetic['Id'].isin(ids)]
        images_synthetic = np.empty([examples.shape[0], self._image_width, self._image_height, 1], dtype=float)
        i = 0
        for _, example in examples.iterrows():
            images_synthetic[i] = np.reshape(example.Image, (self._image_width, self._image_height, 1))
            i += 1

        ids_synthetic = self._df_synthetic.Id[self._df_synthetic.Id.isin(ids)].values

        labels_real = self._df[self._class_columns][self._df['Id'].isin(ids)].values
        labels_synthetic = self._df_synthetic[self._class_columns][self._df_synthetic.Id.isin(ids)].values
        return Batch(np.concatenate((images_real, images_synthetic), axis=0),
                     np.concatenate((labels_real, labels_synthetic), axis=0),
                     np.concatenate((ids_real, ids_synthetic), axis=0))

    def set_predictions(self, ids, predictions):
        super(DatasetPDAugmented, self).set_predictions(ids, predictions)
        for i, _id in enumerate(ids):
            try:
                ix = self._df_synthetic.loc[self._df_synthetic.Id == _id].index
                self._df_synthetic.loc[ix, self._prediction_columns] = predictions[i]
            except TypeError:
                pass

    def evaluate(self):
        confusion_matrix = cf.ConfusionMatrix(self._df[self._prediction_columns].values + self._df_synthetic[self._prediction_columns].values,
                                              self._df[self._class_columns].values + self._df_synthetic[self._class_columns].values)
        confusion_matrix.print_to_console()
