import argparse
import sys
import subprocess
from math import ceil
from multiprocessing import Pool
from functools import partial
import os
import random


def handle(executable, root_path, save_path, obj_per_img, jobs):
    base_path = os.path.dirname(os.path.abspath(__file__))
    n = 20
    sub_groups = [jobs[i:i + n] for i in range(0, len(jobs), n)]
    for sub_jobs in sub_groups:
        print("%d request to run a new subprocess" % os.getpid())
        subprocess.run((
            executable,
            os.path.join(base_path, "make_dataset.py"),
            "--obj_per_img",
            str(obj_per_img),
            "--root",
            root_path,
            "--save",
            save_path,
            "--jobs",
            ",".join(sub_jobs)
        ), check=True)


def main():
    executable = sys.executable
    parser = argparse.ArgumentParser()
    parser.add_argument('--process', default=4, dest="process", type=int)
    parser.add_argument('--obj_per_img', default=20, dest="obj_per_img", type=int)
    parser.add_argument('--root', required=True, dest="root", type=str)
    parser.add_argument('--dataset_name', default="train", dest="dataset_name", type=str)
    parser.add_argument('--start', default=0, dest="start", type=int)
    parser.add_argument('--end', required=True, dest="end", type=int)
    args, _ = parser.parse_known_args()

    end_num = args.end
    max_zeros = len(str(end_num))
    jobs = list(map(lambda x: str(x).zfill(max_zeros), range(args.start, end_num + 1)))
    random.shuffle(jobs)

    nb_process = args.process

    root_path = args.root
    if not os.path.isdir(root_path):
        os.makedirs(root_path)

    save_path = os.path.join(root_path, args.dataset_name)
    if not os.path.isdir(save_path):
        os.makedirs(save_path)

    # How many frames each process should have
    n = ceil(len(jobs) / nb_process)

    # divide frames between processes
    job_groups = [jobs[i:i + n] for i in range(0, len(jobs), n)]

    with Pool(nb_process) as p:
        func = partial(handle, executable, root_path, save_path, args.obj_per_img)
        p.map(func, job_groups)


if __name__ == '__main__':
    main()
