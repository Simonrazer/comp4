import os #Access to Filesystem
import re   #Regex
import toml #TOML File Format
from threading import Thread, Lock #Multithreading
import subprocess   #Sys commands
import time #Debug runtime info
from argparse import ArgumentParser
import shutil

#TODO: Subprojects
#If a comp.toml file is found within a folder, everything there should be compiled with those definitions
#But the output should still be at the top level.
#The most elegant way is to just call comp4.py for that folder again, with some argument that it should output
#to another build folder
#That requires the Subproject not to include anything from not-sub folders, which is fair I think
#The top level comp4 would need to note not to compile the src files from there itself
# -> Another Attribute to the File Class?

#TODO: Argument to change which *-comp.toml file to use
#TODO: Argument to set which *-comp.toml file a subproject should use

#TODO: Refactor into multiple Files
#The overall structure is totally fine I think, but this file is getting a little cramped

#TODO: Maybe allow multiple definitions af same-named source file? Seems to be necessary for some Rodos branches..
#At least C and ASM Code has the same filename there

launch_time = time.time()
parser = ArgumentParser()
#TODO: -F and -D dont make sense, as Files in non-Lib target folders become .os anyways
#TODO: Check if arguments and Target/Exclude definitions dont match
#TODO: Make avaivable in comp.toml
parser.add_argument('-F', '--files-extra', nargs='+', default=[], help="Define more source files to take into Compilation")
parser.add_argument('-L', '--lib-folders', nargs='+', default=[], help="Compile the argument each into one .a")
parser.add_argument('-D', '--files-dirs', nargs='+', default=[], help="Define Folders that will be compiled into a .o")
parser.add_argument('--no-ccache', action='store_false', help="Use ccache to speed up compilation via caching. Needs to be installed, will not be used if not avaivable.")
parser.add_argument('--print-structure', action='store_true', help="Display a file tree in colors describing the status of each subdir in the project")
args = parser.parse_args()

#Clean -lib-folders args of trailing '/'
for i in range(len(args.lib_folders)):
    if args.lib_folders[i].endswith(os.path.sep):
        args.lib_folders[i] = args.lib_folders[i][:-1]

print_structure = args.print_structure
main_directory = os.getcwd() #Calling directory is programm directory! TODO: This needs to change for subprojects

#create build dir structure
if os.path.exists(os.path.join(main_directory, "build")):
    shutil.rmtree("build")

os.mkdir(os.path.join(main_directory, "build"))
os.mkdir(os.path.join(main_directory, "build", "obj"))

for L in args.lib_folders:
    if not os.path.exists(os.path.join(main_directory, "build", L)):
        os.mkdir(os.path.join(main_directory,"build",L))#
if len(args.lib_folders) > 0:
    os.mkdir(os.path.join(main_directory,"build","lib"))

cached_include_paths = {}
if os.path.exists(os.path.join(main_directory, "comp_cache")):
    cached_include_paths = toml.load(os.path.join(main_directory, "comp_cache"))

src_files = {} #files that will be included in compilation, dict of {filename_no_ext : File}
header_files = {} #header files included with -I, dict of {path/filename : File}
raw_files = [] #all files with interesting fileendings in the project directories, list of absolute Filepaths
excluded_files = [] #files with interesting fileending, that were stored in a folder set to be excluded
default_include_choices = {} #stores answer if, and which specific header file is supposed to be included for each file by default
                            #or if the user should be asked to make a choice between multiple options again
                            #TODO: Figure out way to store that between runs?
                                # Or tell the user to just give his files better names so no ambiguity is there


error_match = re.compile(r"fatal error: \\?\"?'?([_a-zA-Z0-9\./-]+)")

#searches for a file with name `filename` in raw_files, or any other list
def find_file_locations(filename, place = raw_files):
    result = []
    for f in place:
        if f.endswith(os.path.sep+filename) or f == filename:    
            result.append(os.path.split(f)[0])
    return result

#returns the library a path is located in, or False if in none
def is_path_in_lib(path):
    for p in args.lib_folders:
        if path.startswith(os.path.join(main_directory, p)):
            return p
    return False

#utillity function, takes a list of {"f": File, "named_as":"filename"} and returns only a list of paths to all Files
def includelist_to_pathlist(fl):
    res = []
    for f in fl:
        res.append(str(f["f"]))
    return res

def abspath_to_relpath(f, nm):
    pt = str(f)
    ln = len(nm)
    arg = pt[:-ln]
    return arg

#returns the needed compiler/falgs for a given file
def get_comp_flags(f):
    if f.ext in srcC_fileendings:
        comp = ccomp
        flags = " "+ (" ".join(cflags))
    elif f.ext in srcCpp_fileendings or f.ext in header_fileendings:
        comp = cppcomp
        flags = " "+ (" ".join(cppflags))
    else:
        print("It feels wrong to not have an 'else'-part for this. This will never run.")
        exit()
    return(comp, flags)

proc_time = 0 #Time spent waiting for preprocessor results
class File:
    def __init__(self, name, path, reas, modtime = None):
        self.name = name #filename with extension
        n_e = os.path.splitext(name)
        self.name_no_ext = n_e[0]
        self.path = path #filepath without filename
        self.ext = n_e[1] #fileextension
        self.reason = reas #which file requires the inclusion of this file, may also be the a target folder
        self.include_string = "" #safes a the include string of this file, so it doesn't
                                            #need to be calculated again every time this file is included
        self.lock = Lock() #Multithreading Lock for this file, used for build_include_string
        self.compiled_to = is_path_in_lib(path)
        if modtime == None:
            self.modtime = os.path.getmtime(os.path.join(path, name))
        else:
            self.modtime = modtime
        pass

    #self to string, used for printing
    def __str__(self) -> str:
        return os.path.join(self.path, self.name)
    
    #searches for neccessary files using the preprocessor. Returns those in a list. Uses a caching system
    def fill_includes(self):
        global proc_time

        inc_ret_list = []
        inc_cache_list = []

        #Takes a list of paths, puts them into inc_ret_list as files, and inc_cache_list as strings, if they need to be included manually
        def combine(il):
            for i in il:
                if i.startswith(main_directory):
                    if i in header_files:
                        inc_ret_list.append(header_files[i])
                        inc_cache_list.append(str(header_files[i]))
                    else:
                        fs = os.path.split(i) 
                        file = File(fs[1], fs[0], str(self))
                        header_files[i] = file
                        inc_ret_list.append(file)
                        inc_cache_list.append(str(file))

        #Caching mechanism
        outdated = False
        if str(self) in cached_include_paths and cached_include_paths[str(self)]["T"]>self.modtime:
            for f in cached_include_paths[str(self)]["I"]:
                cache_time = cached_include_paths[f]["T"]
                if f in header_files:
                    file_time = header_files[f].modtime
                else:
                    if os.path.exists(f):
                        file_time = os.path.getmtime(f)
                    else: file_time = cache_time + 500 #File doenst exist anymore!
                if cache_time < file_time:
                    outdated = True
                    break
        else:
            outdated = True
        if not outdated:
            self.include_string = cached_include_paths[str(self)]["S"]
            combine(cached_include_paths[str(self)]["I"])
            cached_include_paths[str(self)] = {"T": time.time(), "I":inc_cache_list, "S": self.include_string}
            return inc_ret_list

        c_f = get_comp_flags(self)
        cmd = "echo | "+c_f[0]+" -E -M -MM -Wno-everything "+str(self) + self.include_string+ " "+c_f[1]
        dt = time.time()
        output = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        proc_time += time.time() - dt
        while output.stderr:
            error_list = error_match.findall(output.stderr)
            if len(error_list) > 0 :
                looking_for_file = error_match.findall(output.stderr)[0]
            else:
                print("PROBLEM IN PREPRO. PARSING:")
                print(error_list)
                print(self)
                exit()

            if looking_for_file[0] == '\'' or looking_for_file[0] == '"':
                looking_for_file = looking_for_file[1:-1]

            #TODO: ../../ constructions dont work!!
            filesearch = re.compile("/"+looking_for_file+'\s*$')
            
            non_target_matches = [] #List of Files that weren't choosen because a fitting file in a target folder was present, only used for user printout
            choosen_file = None #Placeholder for the include file object {"f": File, "named_as":"filename", "is_raw":False}
            mark_as_choosen = False #Save the users answero to weather or not include a found file by default, if it is needed again
            #check if looking_for_file has a default include set, so the user wouldn't need to be asked again
            #if this is the case, it is
            if looking_for_file in default_include_choices:
                choosen_file = default_include_choices[looking_for_file]

            #Look for files in already already listed header files, and the whole project
            else:
                possible_files = [] #list of matches, either {"f":File, "is_raw":False} or {"f":"path/name", "is_raw":True}
                already_inc_paths = []
                
                #try to find match in header_files
                for path_name, existing_file in header_files.items():
                    if filesearch.findall(path_name): #if a listed header file matches the we are looking for
                        possible_files.append({"f":existing_file, "is_raw":False}) #save it as possibility
                        already_inc_paths.append(path_name) #save that we already found that file, so we wont match it in raw_files again

                #try to match in raw_files
                for abs_path in raw_files:
                    if abs_path not in already_inc_paths and filesearch.findall(abs_path):
                        possible_files.append({"f":abs_path, "is_raw":True})

                excluded_matches = [] #Files that would match, but are in an excluded directory
                for abs_path in excluded_files:
                    if filesearch.findall(abs_path):
                        excluded_matches.append(abs_path)

                #ERROR: No matching files were found anywhere
                if len(possible_files) == 0:
                    print("ERROR: File", looking_for_file,"is required by",self, "but no file like this is in this project")
                    if len(excluded_matches) > 0:
                        print("Matches were found in excluded folders:")
                        for p in excluded_matches:
                            print(p)
                    exit()

                #Check how many matches there are in target folders
                num_files_in_target_folder = 0
                single_matching_file_in_target = None
                
                for f in possible_files:
                    if not f["is_raw"] and f["f"].path in targets:
                        num_files_in_target_folder+=1
                        single_matching_file_in_target = f
                    else:
                        non_target_matches.append(str(f["f"]))

                #If it is only one, take that one                    
                if num_files_in_target_folder == 1:
                    choosen_file = single_matching_file_in_target

                #There was one matching header file found in Neutral Files, taking that one
                elif len(possible_files) == 1:
                    choosen_file = possible_files[0]

                #Multiple option were found in target folders and/or raw_files, asking user what to do
                else:
                    print("File ",self,"requires",looking_for_file,". Multiple possibilities were found:")
                    ii = 0
                    for pf in possible_files:
                        print("(" + str(ii) +") " + str(pf["f"]))
                        if not pf["is_raw"]:
                            print("\tAlso in use by "+pf["f"].reason)
                        ii+=1

                    inp = -1
                    while inp > len(possible_files)-1 or inp < 0:
                        try:
                            inp = int(input("Press the preceding index to include the file\n"))  
                        except ValueError:
                            inp = -1  

                    choosen_file = possible_files[inp]

                    #Ask the user wether to use this file by default in the future or not
                    inp = "i"
                    while inp.capitalize() != "Y" and inp.capitalize() != "N":
                        inp = input("Include this file by default? y/n\n")
                    if inp.capitalize() == "Y":
                        mark_as_choosen = True

            #Adding the file
            f = choosen_file["f"]
            self.include_string+=" -I "+ abspath_to_relpath(f, looking_for_file)
            if mark_as_choosen:
                default_include_choices[looking_for_file] = {"f":f, "named_as": looking_for_file, "is_raw":False}

            #Print info about ignored None-Target Matches, if there are any
            if len(non_target_matches) > 0 and num_files_in_target_folder == 1:
                print("Ignored None-Target matches for needed file",looking_for_file, "as file in target folder", choosen_file["f"], "was found:")
                for nm in non_target_matches:
                    print("\t"+nm)

            c_f = get_comp_flags(self)
            cmd = "echo | "+c_f[0]+" -E -M -MM -Wno-everything "+str(self) + self.include_string+ " "+c_f[1]
            dt = time.time()
            output = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            proc_time += time.time() - dt

        inc_list = output.stdout.split()
        if len(inc_list) >= 3:
            inc_list = inc_list[3:]

        if len(inc_list) > 0:
            combine(inc_list)
            
        cached_include_paths[str(self)] = {"T": time.time(), "I":inc_cache_list, "S": self.include_string}
        return inc_ret_list

#every sourcefile in target will be compiled into a .o file, and ressources for that will be searched for primarily (=> no duplicate names of src files allowed)
#in all header files located in (sub) folders of any target folder
#header files outside a target folder will also be searched, but if a single match in a target folder is found that one will be used
#if there are multiple options that lie in a target folder, the user will be asked to make a choice (see fill_includes)
#sourcefiles for headerfiles will be searched for primarily in target folders. if none are found there, none will be used
#if a/multiple are found outside a target folder, the user will be asked to make a choice
targets = []

#files in excluded folders will not be put in raw_files, but in excluded_files
#if there is no match for a needed file in other valid folders, but one is found in an excluded folder, the user will be told about that
excludes = []

#fileendings describe how a file should be treated (compiler used, is header/sourcefile)
#defaults are amended by the given comp.toml file
#TODO:Currently only appending to these definitions, make a way to override them
srcC_fileendings = {".c", ".s", ".S"}
srcCpp_fileendings = {".cpp", ".cc"}
header_fileendings = {".h", ".hpp"}

cflags = []
cppflags = []
linkerflags = []
ccomp = "clang"
cppcomp = "clang++"
default_c_comp = True
default_cpp_comp = True
use_ccache = args.no_ccache

#TODO: Also allow that to be command arguments?
if os.path.exists(os.path.join(main_directory, "comp.toml")):
    with open(os.path.join(main_directory, "comp.toml"),"r") as fi:
        defintitions = toml.load(fi)
        if "TARGETS" in defintitions:
            targets.extend([os.path.join(main_directory, x) for x in defintitions["TARGETS"]])
        else:
            print("No Targets defined! Using entire directory")
            targets.append(main_directory)
        if "EXCLUDES" in defintitions:
            excludes.extend([os.path.join(main_directory, x) for x in defintitions["EXCLUDES"]])
        #Flags can either be a list of strings, or just a long string. We'll convert it to a list with a single element in the second case
        if "CFLAGS" in defintitions:
            tmp_flags = defintitions["CFLAGS"]
            if type(tmp_flags) == str:
                cflags.append(tmp_flags)
            elif type(tmp_flags) == list:
                for fl in tmp_flags:
                    if fl == "CPPFLAGS":
                        cflags.extend(cppflags)
                    elif fl == "LINKERFLAGS":
                        cflags.extend(linkerflags)
                    else:
                        cflags.append(fl)
        if "CPPFLAGS" in defintitions:
            tmp_flags = defintitions["CPPFLAGS"]
            if type(tmp_flags) == str:
                cppflags.append(tmp_flags)
            elif type(tmp_flags) == list:
                for fl in tmp_flags:
                    if fl == "CFLAGS":
                        cppflags.extend(cflags)
                    elif fl == "LINKERFLAGS":
                        cppflags.extend(linkerflags)
                    else:
                        cppflags.append(fl)
        if "LINKERFLAGS" in defintitions:
            tmp_flags = defintitions["LINKERFLAGS"]
            if type(tmp_flags) == str:
                linkerflags.append(tmp_flags)
            elif type(tmp_flags) == list:
                for fl in tmp_flags:
                    if fl == "CPPFLAGS":
                        linkerflags.extend(cppflags)
                    elif fl == "CFLAGS":
                        linkerflags.extend(cflags)
                    else:
                        linkerflags.append(fl)
        
        if "HEADER" in defintitions:
            header_fileendings=header_fileendings.union(defintitions["HEADER"])
        if "C" in defintitions:
            srcC_fileendings=srcC_fileendings.union(defintitions["C"])
        if "CPP" in defintitions:
            srcCpp_fileendings=srcCpp_fileendings.union(defintitions["CPP"])
        if "CCOMP" in defintitions:
            ccomp = defintitions["CCOMP"]
            default_c_comp = False
        if "CPPCOMP" in defintitions:
            cppcomp = defintitions["CPPCOMP"]  
            default_cpp_comp = False  
else:
    print("No Targets defined! Using entire directory")
    targets.append(main_directory)

#Check inputs for errors
src_fileendings = srcC_fileendings.union(srcCpp_fileendings)
allowed_fileendings = src_fileendings.union(header_fileendings)

if srcC_fileendings & srcCpp_fileendings:
    print("ERROR: overlapping C and Cpp fileendings. Choose one or the other")
    exit()

if header_fileendings & src_fileendings:
    print("ERROR: overlapping Header and Source fileendings. Choose one or the other")
    exit()

def silent_cmd(c):
    p = subprocess.Popen(c, shell=True, stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
    return p

if default_c_comp:
    print("No CCOMP was given, using clang")
    process = silent_cmd(ccomp+ " -v")
    process.wait()
    if process.returncode != 0:
        print("Error finding clang! Looking for gcc instead")
        process = silent_cmd("gcc -v")
        process.wait()
        if process.returncode != 0:
            print("Error finding gcc! Install either clang or gcc and give terminals access to it")
            exit()
        print("Found gcc, using that")
        clang = "gcc"
else:
    process = silent_cmd(ccomp+ " -v")
    process.wait()
    if process.returncode != 0:
        print("The demanded C-Compiler", ccomp,"wasn't found! Aborting")
        exit()

if default_cpp_comp:
    print("No CPPCOMP was given, using clang++")
    process = silent_cmd(cppcomp+ " -v")
    process.wait()
    if process.returncode != 0:
        print("Error finding clang++! Looking for g++ instead")
        process = silent_cmd("g++ -v")
        process.wait()
        if process.returncode != 0:
            print("Error finding g++! Install either clang++ or g++ and give terminals access to it")
            exit()
        print("Found gcc, using that")
        clang = "gcc"
else:
    process = silent_cmd(cppcomp+ " -v")
    process.wait()
    if process.returncode != 0:
        print("The demanded Cpp-Compiler", cppcomp,"wasn't found! Aborting")
        exit()

if use_ccache:
    process = silent_cmd("ccache -V")
    process.wait()
    if process.returncode != 0:
        print("Couldn't find ccache! Not using it")
    else:
        ccomp = "ccache "+ccomp
        cppcomp = "ccache "+cppcomp

#walk down the directory tree, marking every target folder in targets, excluded folder in excludes
#and adding files with interesing extensions to raw_files/excluded_files respectively
#root = current folder
#dirs = dirs contained in current folder
#files = files contained in current folder
for root, dirs, files in os.walk(main_directory, topdown=True):
    to_exc = False
    #Note excluded files
    if root in excludes:
        #Makes the path absolute and checks for interesing extensions
        excluded_files.extend([os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in allowed_fileendings])
        to_exc = True
    if to_exc:
        for d in dirs:
            pt = os.path.join(root, d)
            #Mark subfolders as excluded too, but only if they aren't targets
            if pt not in targets:
                excludes.append(pt)
        continue
    else:
        #Note as raw_file
        raw_files.extend([os.path.join(root, fe) for fe in files if os.path.splitext(fe)[1] in allowed_fileendings])
        if root in targets:
            for d in dirs:
                #... add the subfolders as Targets too, if they aren't excluded
                pt = os.path.join(root, d)
                if pt not in excludes:
                    targets.append(os.path.join(root, d))
    if print_structure:
        offset = "-" * (root.count(os.path.sep)-main_directory.count(os.path.sep))
        if root in targets:
            print("\033[0;32m",offset+root)
        elif root in excludes:
            print("\033[0;31m",offset+root)
        else:
            print("\033[0;37m",offset+root)
print("\033[0;0m")
#Fill src_files and header_files with all src/header files(as File objects) in target folders
for raw_file in raw_files:
    #Only deal with files of target folders
    sp = os.path.split(raw_file)
    path = sp[0]
    name = sp[1]
    if path not in targets:
        continue
    
    f = File(name, path, "Build target "+path)

    if f.ext in src_fileendings:
        if f.name_no_ext in src_files:
            print("ERROR139: File", name, "defined twice: ")
            print(raw_file, "\n\tRequired by Build target "+path)
            print(src_files[f.name_no_ext], "\n\tRequired by "+src_files[f.name_no_ext].reason)
            exit()
        src_files[f.name_no_ext] = f

    elif f.ext in header_fileendings:
        if name in header_files:
            print("This is impossible, header_files is keyed by absolute paths")
            exit()
        n = os.path.join(sp[0], name)
        header_files[n] = f
    
not_needed_src = {} #dict of header files that dont need source files {path/name:True}

#initialize search for ressources
header_additions = [] #We are only looking at source-files in the beginning, to not deal with Header files that aren't needed
                      #The needed ones will get filled as well by the following algorithm
src_additions = list(src_files.values())#all src files as list
all_additions = src_additions.copy()#all files
checked_header_files = []
counter = 0
#until we dont add any more ressources
s_t = time.time()
while all_additions:
    counter+=1
    #print("ROUND", counter)

    #find all missing header files for src+header files 
    new_header_additions = []
    #Only go through src_additions to ignore unneded Header Files
    for file in all_additions:
        included_files = file.fill_includes()
        for i_f in included_files:
            if i_f not in checked_header_files:
                checked_header_files.append(i_f) #don't compute a file mutiple times
                new_header_additions.append(i_f) #only check them in the next run, not any other following ones

    new_src_additions = [] #list of new Files added as src files

    #look if there is a src file for a header we dont have in src yet
    for file in new_header_additions:   
        if file.name_no_ext in src_files: 
            #Found one sourcefile already present in a target folder for this header, all is well
            continue

        matches = [] #list of all matching FILES
        excluded_matches = [] #List of paths that were excluded due tu exclude folder definitions
        #search raw files if nothing was found in already crawled sources
        for ext in src_fileendings:        
            srcname = file.name_no_ext+ext
            found_file_locs = find_file_locations(srcname)
            for ff in found_file_locs:
                #Create a File object and add it to possible matches
                matches.append(File(srcname, ff, file))
            lll = find_file_locations(srcname, excluded_files)
            excluded_matches.extend(find_file_locations(srcname, excluded_files))

        if matches:
            #Found source files in raw_files, ask the user if/which one to include
            print("Following source files for header file", file.name, "with matching names were found")          
            i = 0
            for match in matches:
                print("("+str(i)+") ",match)
                i+=1
            inp = -1
            
            while inp > len(matches)-1 or inp < 0:
                k = input("Press the preceding index to include the file, x for none\n")
                if k == 'x':
                    not_needed_src[str(file)] = True
                    break
                inp = int(k)

            if str(file) in not_needed_src:
                print("Okay, not adding a source file for", file.name)
            else:
                print("Adding", matches[inp], "to compilation")
                src_files[matches[inp].name_no_ext] = matches[inp]
                new_src_additions.append(matches[inp])

        else:
            print("No matching source files were found for", file.name)
            if len(excluded_matches)>0:
                print("Matches were found in excluded Folder:")
                for p in excluded_matches:
                    print(p)

    #Update additions to handle these ones in the next round
    #TODO: Not 100% sure if .copy is necessary, need to look again if the list is actually getting modified
    header_additions = new_header_additions.copy()
    src_additions = new_src_additions.copy()
    all_additions = new_header_additions.copy()
    all_additions.extend(src_additions)
print("Done in",time.time()-s_t,"s")   
print("Process Time:", proc_time)

#build it
print("Generating object files")
s_t = time.time()
#TODO: Failure analysis on clang error?


def gen_o(src_files_slice):
    #This is multithreaded! But no writing to shared variables happens here, and no Files in src_files_slice
    #Are handeled by 2 Threads (ensured by get_chunks())
    vsc_includes = '' #prototype VSC autocomplete include string
    for f in src_files_slice:
        comp, flags = get_comp_flags(f)

        include_string = f.include_string
        #include_string = f.build_include_string(include_string)
        for s in include_string.split(" -I "):
            if s in vsc_includes:
                continue
            vsc_includes+='"'+s+'",'
        if f.compiled_to:
            dir = os.path.join(main_directory, "build", f.compiled_to)
        else:
            dir = os.path.join(main_directory, "build", "obj")
        cmd = comp +" "+include_string + " -c "+os.path.join(f.path, f.name) + " -o " + os.path.join(dir, f.name_no_ext+".o")+flags+"\n"
        output = subprocess.run(cmd, shell=True)

#Return the list l split into n chunks, ensuring that no element is listed twice, or is left out
def chunks(l, n):
    res = []
    for i in range(0, n):
        res.append(l[i::n])
    return res

N = 8 #Number of Threads to use, deminishing results when using over 8 (Tested on 12 Thread CPU)
to_comp = chunks(list(src_files.values()),N) #Split source files to compile into N lists
threads = []
for i in range(N): #Start N threads, each working on compiling a list
    thread = Thread(target = gen_o, args = (to_comp[i],))
    threads.append(thread)
    thread.start()
for i in range(N): #Wait for them all to finish
    threads[i].join()

print("Done in",time.time()-s_t,"s")

print("Bundeling Libraries")

#Bundle each declared library into a .a
for L in args.lib_folders:
    subprocess.run("ar rc "+os.path.join(main_directory,"build","lib", L+".a")+" "+os.path.join(main_directory, "build", L,"*"), shell=True)

print("\nGenerating Executable")
s_t = time.time()
all_os = ""
libs = ""
for ff, f in src_files.items():
    if f.compiled_to:
        libs += os.path.join(main_directory, "build", "lib", f.compiled_to+".a") +" "
    else:
        all_os += os.path.join(main_directory, "build", "obj",f.name_no_ext+".o") +" "
    
#TODO: Make it adjustable which compiler should do the linking?
#TODO: Compare filesizes of executables
cmd = cppcomp+" "+  all_os+libs+" "+(" ".join(cppflags)) +" "+  (" ".join(linkerflags))
subprocess.run(cmd, shell=True)
print("Done in",time.time()-s_t,"s")

print("Saving Include Path Caches")

output_file_name = "comp_cache"
with open(output_file_name, "w") as toml_file:
    toml.dump(cached_include_paths, toml_file)