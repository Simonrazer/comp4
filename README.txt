TESTING VERSION! MOST IS SUBJECT TO CHANGE

README of a compile Tool for C/C++ projects, working-title comp4.py

Dependencies:
    pip install toml
    pip install argparse
    any C/C++ compiler, tested with clang/++ , gcc/g++/c++
    ar - command must be avaivable (Is installed by default on all Linux Distros)

optional Dependencies:
    ccache - command needs to be avaivable, for Linux just download the bin (https://ccache.dev/download.html),
        extract it, and place it in /bin

USAGE:
Place comp4.py anywhere you like. You may create Symbolic links to it.
With the terminal placed at the top of your project, run 
    python3 path/to/comp4.py ARGS

Where ARGS are arguments as described below. 

comp4.py makes default assumptions, so very little configuration is required.
Confguration can be done via a file named comp.toml placed at the top of the project, or via Command Lina Arguments
! Arguments and comp.toml do not have Feature parity yet !

By default, the enitre Project structure is compiled.
By definig TARGETS = ["path/to/srcs","...] in comp.toml you may override that behaviour, so only the given paths and their
subdirectories will be compiled.
Source-Files with the same name are not allowed to be compiled Together.

If a File that needs to be compiled includes a file outside a Folder defined in TARGETS, that file will be found and taken.
If a Header File is included that does not have a source file of the same name included in the compilation, the Project will be searched for one.
If one/multiple are found the User will be asked if/which one to include.
If there is ambiguity which Header-File a File wants to include, the User will be asked.
If a single matching Header-File exists in a Target Folder, that one wil be used.
If there are Directories in your Project you don't want to include in Compilation at all, define them in comp.toml with EXCLUDES = ["path", "...]

Other avaivable Definitions in comp.toml are:
CCOMP = "x"
CPPCOMP = "x
    -define the C/C++ compiler to be used, default is clang/clang++, with a fallback to gcc/g++

CFLAGS = "xxx.." / CFLAGS = ["xx","xx..]
CPPFLAGS = "xxx.." / CPPFLAGS = ["xx","xx..]
CLINKERFLAGSFLAGS = "xxx.." / LINKERFLAGS = ["xx","xx..]
    -define the Flags to be used for C/C++ compilation, and at the Linking step. Definition as a String or as a List of strings is valid.
    It is possible to include Flags of one Category in the other, for example:
        CFLAGS = "-m32"
        CPPFLAGS = ["CFLAGS", "-g"]

HEADER = [".hpp", ".x...]
C = [".xx", ".x...]
CPP = [".xx", ".x...]
    -ammends the default definitions of which File-extensions to treat as which File-Type. Overriding the defaults is not yet supportet.
    Defaults are:
        srcC_fileendings = {".c", ".s", ".S"}
        srcCpp_fileendings = {".cpp", ".cc"}
        header_fileendings = {".h", ".hpp"}

Command-Line Arguments are:
-L path/to/srcs [path/to/srcs]
    -define folders that will each be compiled into an archive before linking
--print-structure
    -Display a file tree in colors describing the status of each subdirectory in the project (Red = Excluded, Green = Target, White = Neutral)
--no-ccache
    -Don't use ccache to speed up compilation via caching. Needs to be installed, will not be used if not avaivable.

comp4.py will create a build directory at the top of the project. It will be deleted and recreated on every run, so don't put anything in there.
comp4.py will create a file called comp_cache at the top of the project. There cached include paths will be stored. It can be deleted safely.